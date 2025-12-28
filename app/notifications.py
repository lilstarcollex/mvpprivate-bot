from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import aiosqlite
from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter


class NotificationService:
    """Separate bot for sending structured lead notifications."""

    def __init__(self, token: str, chat_id: int, db_path: Path) -> None:
        self.token = token
        self.chat_id = chat_id
        self.db_path = Path(db_path)
        self._bot: Optional[Bot] = None
        self._session: Optional[AiohttpSession] = None
        self._db: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
        """Initialize bot session for notifications."""
        if self._bot:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._ensure_table()
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
        if self._db:
            await self._db.close()
            self._db = None

    async def send_lead(self, payload: Dict[str, Any]) -> None:
        """Send structured lead data to notification chat and persist history."""
        if not self._bot:
            raise RuntimeError("NotificationService is not started")

        text = self._format_payload(payload)
        await self._save_lead(payload)
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

    async def fetch_leads(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """Fetch last leads for history."""
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, created_at, name, use_case, custom_task, extra, telegram_id, username
            FROM leads
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def count_leads(self) -> int:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        cursor = await self._db.execute("SELECT COUNT(*) as cnt FROM leads")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["cnt"] if row and "cnt" in row.keys() else 0)

    async def has_lead(self, telegram_id: int) -> bool:
        """Check if this user already submitted a lead."""
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        cursor = await self._db.execute(
            "SELECT 1 FROM leads WHERE telegram_id = ? LIMIT 1",
            (str(telegram_id),),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return bool(row)

    @property
    def bot(self) -> Bot:
        if not self._bot:
            raise RuntimeError("NotificationService is not started")
        return self._bot

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

    async def _ensure_table(self) -> None:
        assert self._db
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                name TEXT,
                use_case TEXT,
                custom_task TEXT,
                extra TEXT,
                telegram_id TEXT,
                username TEXT
            );
            """
        )
        # Deduplicate by telegram_id (keep the latest) and add unique index.
        await self._db.execute(
            """
            DELETE FROM leads
            WHERE telegram_id IS NOT NULL
              AND id NOT IN (
                SELECT MAX(id) FROM leads WHERE telegram_id IS NOT NULL GROUP BY telegram_id
            );
            """
        )
        await self._db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_telegram_id
            ON leads(telegram_id) WHERE telegram_id IS NOT NULL;
            """
        )
        await self._db.commit()

    async def _save_lead(self, payload: Dict[str, Any]) -> None:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")

        await self._db.execute(
            """
            INSERT INTO leads (name, use_case, custom_task, extra, telegram_id, username)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("name"),
                payload.get("use_case"),
                payload.get("custom_task"),
                payload.get("extra"),
                str(payload.get("telegram_id")) if payload.get("telegram_id") else None,
                payload.get("username"),
            ),
        )
        await self._db.commit()


__all__ = ["NotificationService"]
