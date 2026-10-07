import os
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiosqlite

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    credits INTEGER NOT NULL DEFAULT 0,
    referred_by INTEGER,
    total_spent INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    credits INTEGER NOT NULL,
    file_id TEXT,
    file_unique_id TEXT UNIQUE,
    transaction_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    topic TEXT NOT NULL,
    size INTEGER NOT NULL,
    lang TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_tx ON payments(transaction_id);
CREATE TABLE IF NOT EXISTS hand_packs (
    user_id INTEGER PRIMARY KEY,
    data BLOB NOT NULL
);
"""

_pool = None


def pg_sql(sql: str) -> str:
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    sql = re.sub(r"\bINTEGER\b", "BIGINT", sql).replace("BLOB", "BYTEA")
    counter = iter(range(1, 1000))
    sql = re.sub(r"\?", lambda _: f"${next(counter)}", sql)
    if sql.lstrip().upper().startswith("INSERT INTO PAYMENTS"):
        sql += " RETURNING id"
    return sql


def _pg_dsn(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k == "sslmode"])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


class _PgCursor:
    def __init__(self, rows: list, rowcount: int, lastrowid=None):
        self._rows, self.rowcount, self.lastrowid = rows, rowcount, lastrowid

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _PgConn:
    row_factory = None

    async def __aenter__(self):
        self.conn = await _pool.acquire()
        self.tx = self.conn.transaction()
        await self.tx.start()
        return self

    async def __aexit__(self, exc_type, *exc):
        try:
            if exc_type:
                await self.tx.rollback()
            else:
                await self.tx.commit()
        finally:
            await _pool.release(self.conn)

    async def commit(self):
        await self.tx.commit()
        self.tx = self.conn.transaction()
        await self.tx.start()

    async def executescript(self, script: str):
        await self.conn.execute(pg_sql(script))

    async def execute(self, sql: str, args=()):
        q = pg_sql(sql)
        if q.lstrip().upper().startswith("SELECT") or q.endswith("RETURNING id"):
            rows = await self.conn.fetch(q, *args)
            last = rows[0][0] if rows and q.endswith("RETURNING id") else None
            return _PgCursor(rows, len(rows), last)
        status = (await self.conn.execute(q, *args)).split()[-1]
        return _PgCursor([], int(status) if status.isdigit() else 0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _connect():
    if _pool is not None:
        return _PgConn()
    return aiosqlite.connect(config.DB_PATH)


async def init() -> None:
    global _pool
    if config.DATABASE_URL:
        import asyncpg
        _pool = await asyncpg.create_pool(_pg_dsn(config.DATABASE_URL), min_size=0, max_size=5,
                                          max_inactive_connection_lifetime=60,
                                          statement_cache_size=0)
    else:
        os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    async with _connect() as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_or_create_user(user_id: int, username: str | None, full_name: str,
                             referred_by: int | None = None) -> tuple[dict, bool]:
    user = await get_user(user_id)
    if user:
        async with _connect() as db:
            await db.execute("UPDATE users SET username=?, full_name=? WHERE id=?",
                             (username, full_name, user_id))
            await db.commit()
        return user, False
    if referred_by == user_id or (referred_by and not await get_user(referred_by)):
        referred_by = None
    async with _connect() as db:
        await db.execute(
            "INSERT INTO users (id, username, full_name, credits, referred_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, full_name, config.FREE_CREDITS, referred_by, _now()),
        )
        await db.commit()
    return await get_user(user_id), True


async def spend_credit(user_id: int) -> bool:
    async with _connect() as db:
        cur = await db.execute(
            "UPDATE users SET credits=credits-1 WHERE id=? AND credits>0", (user_id,))
        await db.commit()
        return cur.rowcount == 1


async def add_credits(user_id: int, amount: int) -> None:
    async with _connect() as db:
        await db.execute("UPDATE users SET credits=credits+? WHERE id=?", (amount, user_id))
        await db.commit()


async def set_blocked(user_id: int, blocked: bool) -> None:
    async with _connect() as db:
        await db.execute("UPDATE users SET blocked=? WHERE id=?", (int(blocked), user_id))
        await db.commit()


async def referral_count(user_id: int) -> int:
    async with _connect() as db:
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (user_id,))
        return (await cur.fetchone())[0]


async def receipt_used(file_unique_id: str, transaction_id: str | None) -> bool:
    async with _connect() as db:
        cur = await db.execute("SELECT 1 FROM payments WHERE file_unique_id=?", (file_unique_id,))
        if await cur.fetchone():
            return True
        if transaction_id:
            cur = await db.execute(
                "SELECT 1 FROM payments WHERE transaction_id=? AND status='approved'",
                (transaction_id,))
            if await cur.fetchone():
                return True
    return False


async def create_payment(user_id: int, amount: int, credits: int, file_id: str,
                         file_unique_id: str, transaction_id: str | None, note: str) -> int:
    async with _connect() as db:
        cur = await db.execute(
            "INSERT INTO payments (user_id, amount, credits, file_id, file_unique_id, "
            "transaction_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, amount, credits, file_id, file_unique_id, transaction_id, note, _now()),
        )
        await db.commit()
        return cur.lastrowid


async def get_payment(payment_id: int) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM payments WHERE id=?", (payment_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def approve_payment(payment_id: int) -> tuple[dict | None, int | None]:
    """Approve a pending payment. Returns (payment, referrer_id_rewarded)."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "UPDATE payments SET status='approved' WHERE id=? AND status='pending'", (payment_id,))
        if cur.rowcount != 1:
            await db.commit()
            return None, None
        cur = await db.execute("SELECT * FROM payments WHERE id=?", (payment_id,))
        payment = dict(await cur.fetchone())
        uid = payment["user_id"]
        await db.execute(
            "UPDATE users SET credits=credits+?, total_spent=total_spent+? WHERE id=?",
            (payment["credits"], payment["amount"], uid))
        cur = await db.execute(
            "SELECT COUNT(*) FROM payments WHERE user_id=? AND status='approved'", (uid,))
        approved_count = (await cur.fetchone())[0]
        rewarded = None
        if approved_count == 1 and config.REFERRAL_BONUS > 0:
            cur = await db.execute("SELECT referred_by FROM users WHERE id=?", (uid,))
            row = await cur.fetchone()
            if row and row["referred_by"]:
                rewarded = row["referred_by"]
                await db.execute("UPDATE users SET credits=credits+? WHERE id=?",
                                 (config.REFERRAL_BONUS, rewarded))
        await db.commit()
        return payment, rewarded


async def reject_payment(payment_id: int, note: str = "") -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "UPDATE payments SET status='rejected', note=? WHERE id=? AND status='pending'",
            (note, payment_id))
        await db.commit()
        if cur.rowcount != 1:
            return None
        cur = await db.execute("SELECT * FROM payments WHERE id=?", (payment_id,))
        return dict(await cur.fetchone())


async def log_order(user_id: int, kind: str, topic: str, size: int, lang: str, status: str) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT INTO orders (user_id, kind, topic, size, lang, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, kind, topic, size, lang, status, _now()))
        await db.commit()


async def stats() -> dict:
    today = _today()
    async with _connect() as db:
        async def one(sql: str, *args):
            cur = await db.execute(sql, args)
            return int((await cur.fetchone())[0] or 0)
        return {
            "users": await one("SELECT COUNT(*) FROM users"),
            "users_today": await one("SELECT COUNT(*) FROM users WHERE created_at LIKE ?", f"{today}%"),
            "orders": await one("SELECT COUNT(*) FROM orders WHERE status='done'"),
            "orders_today": await one(
                "SELECT COUNT(*) FROM orders WHERE status='done' AND created_at LIKE ?", f"{today}%"),
            "revenue": await one("SELECT SUM(amount) FROM payments WHERE status='approved'"),
            "revenue_today": await one(
                "SELECT SUM(amount) FROM payments WHERE status='approved' AND created_at LIKE ?",
                f"{today}%"),
            "pending": await one("SELECT COUNT(*) FROM payments WHERE status='pending'"),
            "paying_users": await one(
                "SELECT COUNT(DISTINCT user_id) FROM payments WHERE status='approved'"),
        }


async def save_hand_pack(user_id: int, data: bytes) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT INTO hand_packs (user_id, data) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data", (user_id, data))
        await db.commit()


async def load_hand_pack(user_id: int) -> bytes | None:
    async with _connect() as db:
        cur = await db.execute("SELECT data FROM hand_packs WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return bytes(row[0]) if row else None


async def all_user_ids() -> list[int]:
    async with _connect() as db:
        cur = await db.execute("SELECT id FROM users WHERE blocked=0")
        return [r[0] for r in await cur.fetchall()]
