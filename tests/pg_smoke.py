"""Haqiqiy PostgreSQL bilan tekshiruv: python tests/pg_smoke.py "postgresql://..." """
import asyncio
import sys

import config
import db
import handwriting as hw

TEST_ID = 999_000_000_001


async def main(url: str) -> None:
    config.DATABASE_URL = url
    await db.init()
    try:
        user, new = await db.get_or_create_user(TEST_ID, "smoke", "Smoke Test")
        assert user["id"] == TEST_ID
        start = user["credits"]
        await db.add_credits(TEST_ID, 3)
        assert await db.spend_credit(TEST_ID)
        assert (await db.get_user(TEST_ID))["credits"] == start + 2
        pid = await db.create_payment(TEST_ID, 5000, 1, "f", f"smoke-{TEST_ID}", None, "smoke")
        payment, _ = await db.approve_payment(pid)
        assert payment["user_id"] == TEST_ID
        assert (await db.get_payment(pid))["status"] == "approved"
        await db.log_order(TEST_ID, "hand", "smoke", 1, "uz", "done")
        await hw.save_pack(TEST_ID, {"kind": "font", "font": "marck"})
        assert (await hw.load_pack(TEST_ID))["font"] == "marck"
        print("stats", await db.stats())
        print("users", len(await db.all_user_ids()))
        print("OK")
    finally:
        async with db._connect() as conn:
            for table, col in (("hand_packs", "user_id"), ("orders", "user_id"),
                               ("payments", "user_id"), ("users", "id")):
                await conn.execute(f"DELETE FROM {table} WHERE {col}=?", (TEST_ID,))
            await conn.commit()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
