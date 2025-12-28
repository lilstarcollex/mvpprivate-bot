from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.notifications import NotificationService


class SubmissionGuardMiddleware(BaseMiddleware):
    """Blocks repeated submissions; replies with a fixed message."""

    def __init__(self, notification_service: NotificationService, reply_text: str) -> None:
        self.notification_service = notification_service
        self.reply_text = reply_text

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Optional[Any]:
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id:
            if await self.notification_service.has_lead(user_id):
                await self._reply(event)
                return None

        return await handler(event, data)

    async def _reply(self, event: TelegramObject) -> None:
        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(self.reply_text)
        elif isinstance(event, Message):
            await event.answer(self.reply_text)
