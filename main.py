import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiohttp import web

import config
import db
import handlers_admin
import handlers_hand
import handlers_user


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_health_server() -> None:
    port = os.getenv("PORT")
    if not port:
        return
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    logging.info("Health server: %s-port", port)


async def main() -> None:
    missing = [k for k in ("BOT_TOKEN", "GEMINI_API_KEY", "CARD_NUMBER") if not getattr(config, k)]
    if missing:
        sys.exit(f".env faylida to'ldirilmagan: {', '.join(missing)}")
    if not config.ADMIN_IDS:
        logging.warning("ADMIN_IDS bo'sh — to'lov xabarlari hech kimga bormaydi!")

    await db.init()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(handlers_admin.router)
    dp.include_router(handlers_hand.router)
    dp.include_router(handlers_user.router)
    await start_health_server()
    await bot.set_my_commands([BotCommand(command="start", description="Bosh menyu")])
    me = await bot.get_me()
    logging.info("Bot ishga tushdi: @%s", me.username)
    await dp.start_polling(bot)


if __name__ == "__main__":
    os.makedirs(os.path.join(config.BASE_DIR, "data"), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(os.path.join(config.BASE_DIR, "data", "bot.log"), encoding="utf-8")],
    )
    asyncio.run(main())
