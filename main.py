from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties

from app.config import load_config
from app.handlers.main import register_handlers
from app.notifications import NotificationService
from app.storage import SQLiteStorage


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    config = load_config()
    storage = SQLiteStorage(config.storage_path)
    await storage.start()
    notification_service = NotificationService(
        token=config.notification_bot_token,
        chat_id=config.notification_chat_id,
    )
    await notification_service.start()

    bot = Bot(
        token=config.main_bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=storage)
    register_handlers(dp, notification_service)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await notification_service.close()
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
