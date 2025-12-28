from __future__ import annotations

import asyncio
import html
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import aiosqlite
from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter


class NotificationService:
    """Separate bot for sending structured lead notifications and storing history."""

    def __init__(self, token: str, chat_id: int, db_path: Path) -> None:
        self.token = token
        self.chat_id = chat_id
        self.db_path = Path(db_path)
        self._bot: Optional[Bot] = None
        self._session: Optional[AiohttpSession] = None
        self._db: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
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
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, created_at, scenario, name, use_case, custom_task, extra,
                   telegram_id, username, vps_ip, ssh_ok, domain, protocols, bot_token,
                   payment_id, payment_url, payment_status
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
        scenario = html.escape(payload.get("scenario") or "—")
        name = html.escape(payload.get("name") or "—")
        use_case = html.escape(payload.get("use_case") or "—")
        custom_task = html.escape(payload.get("custom_task") or "—")
        extra = html.escape(payload.get("extra") or "—")
        user_id = payload.get("telegram_id") or "—"
        username = payload.get("username")
        username_display = f"@{html.escape(username)}" if username else "—"
        vps_ip = html.escape(payload.get("vps_ip") or "—")
        domain = html.escape(payload.get("domain") or "—")
        ssh_ok = payload.get("ssh_ok")
        ssh_status = "✅" if ssh_ok else ("⚠️" if ssh_ok is not None else "—")
        protocols = payload.get("protocols") or []
        if isinstance(protocols, str):
            try:
                protocols = json.loads(protocols)
            except Exception:
                protocols = [protocols]
        protocols_display = ", ".join(protocols) if protocols else "—"
        bot_token = payload.get("bot_token")
        bot_token_masked = f"{bot_token[:6]}***" if bot_token else "—"
        payment_status = payload.get("payment_status") or "—"
        payment_url = html.escape(payload.get("payment_url") or "—")

        lines = [
            "<b>Новая заявка</b>",
            f"Сценарий: <b>{scenario}</b>",
            f"Имя: <b>{name}</b>",
            f"Тип задачи: <b>{use_case}</b>",
            f"Детали задачи: {custom_task}",
            f"Дополнительная информация: {extra}",
            f"Telegram ID: <code>{user_id}</code>",
            f"Username: {username_display}",
            "",
            f"VPS IP: {vps_ip}",
            f"Domain: {domain}",
            f"SSH доступ: {ssh_status}",
            f"Протоколы: {protocols_display}",
            f"Bot Token: {bot_token_masked}",
            f"Оплата: {payment_status}",
            f"Payment URL: {payment_url}",
        ]
        return "\n".join(lines)

    async def _ensure_table(self) -> None:
        assert self._db
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                scenario TEXT,
                name TEXT,
                use_case TEXT,
                custom_task TEXT,
                extra TEXT,
                telegram_id TEXT,
                username TEXT,
                vps_ip TEXT,
                ssh_ok INTEGER,
                root_password TEXT,
                domain TEXT,
                protocols TEXT,
                bot_token TEXT,
                payment_id TEXT,
                payment_url TEXT,
                payment_status TEXT
            );
            """
        )
        # Add missing columns if DB already exists.
        for column, ddl in [
            ("scenario", "ALTER TABLE leads ADD COLUMN scenario TEXT"),
            ("vps_ip", "ALTER TABLE leads ADD COLUMN vps_ip TEXT"),
            ("ssh_ok", "ALTER TABLE leads ADD COLUMN ssh_ok INTEGER"),
            ("root_password", "ALTER TABLE leads ADD COLUMN root_password TEXT"),
            ("domain", "ALTER TABLE leads ADD COLUMN domain TEXT"),
            ("protocols", "ALTER TABLE leads ADD COLUMN protocols TEXT"),
            ("bot_token", "ALTER TABLE leads ADD COLUMN bot_token TEXT"),
            ("payment_id", "ALTER TABLE leads ADD COLUMN payment_id TEXT"),
            ("payment_url", "ALTER TABLE leads ADD COLUMN payment_url TEXT"),
            ("payment_status", "ALTER TABLE leads ADD COLUMN payment_status TEXT"),
        ]:
            if not await self._has_column(column):
                await self._db.execute(ddl)

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

    async def _has_column(self, name: str) -> bool:
        cursor = await self._db.execute("PRAGMA table_info(leads)")
        rows = await cursor.fetchall()
        await cursor.close()
        return any(row[1] == name for row in rows)

    async def _save_lead(self, payload: Dict[str, Any]) -> None:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")

        protocols = payload.get("protocols") or []
        protocols_json = json.dumps(protocols, ensure_ascii=False)

        await self._db.execute(
            """
            INSERT OR REPLACE INTO leads
            (telegram_id, created_at, scenario, name, use_case, custom_task, extra, username,
             vps_ip, ssh_ok, root_password, domain, protocols, bot_token,
             payment_id, payment_url, payment_status)
            VALUES (?, COALESCE((SELECT created_at FROM leads WHERE telegram_id=?), CURRENT_TIMESTAMP),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(payload.get("telegram_id")) if payload.get("telegram_id") else None,
                str(payload.get("telegram_id")) if payload.get("telegram_id") else None,
                payload.get("scenario"),
                payload.get("name"),
                payload.get("use_case"),
                payload.get("custom_task"),
                payload.get("extra"),
                payload.get("username"),
                payload.get("vps_ip"),
                1 if payload.get("ssh_ok") else 0 if payload.get("ssh_ok") is not None else None,
                payload.get("root_password"),
                payload.get("domain"),
                protocols_json,
                payload.get("bot_token"),
                payload.get("payment_id"),
                payload.get("payment_url"),
                payload.get("payment_status"),
            ),
        )
        await self._db.commit()


__all__ = ["NotificationService"]
