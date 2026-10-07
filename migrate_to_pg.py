"""Lokal SQLite bazani (data/bot.db) PostgreSQL (DATABASE_URL) ga bir marta ko'chiradi.

Ishlatish:  python migrate_to_pg.py "postgresql://..."
"""
import asyncio
import sqlite3
import sys

import asyncpg

import config
import db

TABLES = ("users", "payments", "orders", "hand_packs")


async def main(url: str) -> None:
    config.DATABASE_URL = url
    await db.init()
    src = sqlite3.connect(config.DB_PATH)
    src.row_factory = sqlite3.Row
    conn = await asyncpg.connect(db._pg_dsn(url), statement_cache_size=0)
    try:
        for table in TABLES:
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                print(f"{table}: bo'sh")
                continue
            cols = rows[0].keys()
            sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES "
                   f"({', '.join(f'${i}' for i in range(1, len(cols) + 1))}) ON CONFLICT DO NOTHING")
            await conn.executemany(sql, [tuple(r) for r in rows])
            print(f"{table}: {len(rows)} ta qator")
        for table in ("payments", "orders"):
            await conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
    finally:
        await conn.close()
        src.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit('Ishlatish: python migrate_to_pg.py "postgresql://..."')
    asyncio.run(main(sys.argv[1]))
