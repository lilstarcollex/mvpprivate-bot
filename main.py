from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import load_config
from app.handlers.main import register_handlers
from app.handlers.notification import register_notification_handlers
from app.notifications import NotificationService
from app.payments import PaymentClient
from app.storage import SQLiteStorage


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    config = load_config()
    storage = SQLiteStorage(config.storage_path)
    await storage.start()
    notification_service = NotificationService(
        token=config.notification_bot_token,
        chat_ids=config.notification_chat_ids,
        db_path=config.leads_db_path,
        main_bot_token=config.main_bot_token,
    )
    await notification_service.start()

    payment_client = PaymentClient(
        shop_id=config.yookassa_shop_id,
        secret_key=config.yookassa_secret_key,
        return_url=config.yookassa_return_url,
    )

    bot = Bot(
        token=config.main_bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=storage)
    register_handlers(dp, notification_service, payment_client)

    notify_dp = Dispatcher(storage=MemoryStorage())
    register_notification_handlers(notify_dp, notification_service)

    async def run_main_bot() -> None:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

    async def run_notification_bot() -> None:
        notify_bot = notification_service.bot
        await notify_dp.start_polling(notify_bot, allowed_updates=notify_dp.resolve_used_update_types())

    try:
        await asyncio.gather(run_main_bot(), run_notification_bot())
    finally:
        await notification_service.close()
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
