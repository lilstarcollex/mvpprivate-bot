from __future__ import annotations

import asyncio
import html
import logging
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter


class NotificationService:
    """Separate bot for sending structured lead notifications."""

    def __init__(self, token: str, chat_id: int) -> None:
        self.token = token
        self.chat_id = chat_id
        self._bot: Optional[Bot] = None
        self._session: Optional[AiohttpSession] = None

    async def start(self) -> None:
        """Initialize bot session for notifications."""
        if self._bot:
            return
        self._session = AiohttpSession()
        self._bot = Bot(
            token=self.token,
            session=self._session,
            default=DefaultBotProperties(parse_mode="HTML"),
        )

    async def close(self) -> None:
        """Close underlying HTTP session."""
        if self._bot:
            await self._bot.session.close()
            self._bot = None
        if self._session:
            await self._session.close()
            self._session = None

    async def send_lead(self, payload: Dict[str, Any]) -> None:
        """Send structured lead data to notification chat."""
        if not self._bot:
            raise RuntimeError("NotificationService is not started")

        text = self._format_payload(payload)
        try:
            await self._bot.send_message(chat_id=self.chat_id, text=text, disable_web_page_preview=True)
        except TelegramRetryAfter as exc:
            logging.warning("Notification rate limited, retrying after %ss", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            await self._bot.send_message(chat_id=self.chat_id, text=text, disable_web_page_preview=True)
        except TelegramAPIError as exc:
            logging.error("Failed to send notification: %s", exc)
        except Exception as exc:  # pragma: no cover
            logging.exception("Unexpected error while sending notification: %s", exc)

    def _format_payload(self, payload: Dict[str, Any]) -> str:
        name = html.escape(payload.get("name") or "—")
        use_case = html.escape(payload.get("use_case") or "—")
        custom_task = html.escape(payload.get("custom_task") or "—")
        extra = html.escape(payload.get("extra") or "—")
        user_id = payload.get("telegram_id") or "—"
        username = payload.get("username")
        username_display = f"@{html.escape(username)}" if username else "—"

        lines = [
            "<b>Новая заявка</b>",
            f"Имя: <b>{name}</b>",
            f"Тип задачи: <b>{use_case}</b>",
            f"Детали задачи: {custom_task}",
            f"Дополнительная информация: {extra}",
            f"Telegram ID: <code>{user_id}</code>",
            f"Username: {username_display}",
        ]
        return "\n".join(lines)


__all__ = ["NotificationService"]
