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

    def __init__(self, token: str, chat_ids: list[int], db_path: Path, main_bot_token: Optional[str] = None) -> None:
        self.token = token
        self.chat_ids = list(dict.fromkeys(chat_ids))
        self.db_path = Path(db_path)
        self.main_bot_token = main_bot_token
        self._bot: Optional[Bot] = None
        self._session: Optional[AiohttpSession] = None
        self._db: Optional[aiosqlite.Connection] = None
        self._main_bot: Optional[Bot] = None
        self._main_session: Optional[AiohttpSession] = None
        self._lead_cache: set[str] = set()

    async def start(self) -> None:
        if self._bot:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._ensure_table()
        await self._warm_cache()
        self._session = AiohttpSession()
        self._bot = Bot(
            token=self.token,
            session=self._session,
            default=DefaultBotProperties(parse_mode="HTML"),
        )
        if self.main_bot_token:
            self._main_session = AiohttpSession()
            self._main_bot = Bot(
                token=self.main_bot_token,
                session=self._main_session,
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
        if self._main_bot:
            await self._main_bot.session.close()
            self._main_bot = None
        if self._main_session:
            await self._main_session.close()
            self._main_session = None

    async def send_lead(self, payload: Dict[str, Any]) -> None:
        if not self._bot:
            raise RuntimeError("NotificationService is not started")

        await self._save_user_from_payload(payload)
        text = self._format_payload(payload)
        await self._save_lead(payload)
        for chat_id in self.chat_ids:
            await self._send_message(chat_id, text)

    async def _send_message(self, chat_id: int, text: str) -> None:
        if not self._bot:
            raise RuntimeError("NotificationService is not started")
        try:
            await self._bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        except TelegramRetryAfter as exc:
            logging.warning("Notification rate limited for chat %s, retrying after %ss", chat_id, exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            await self._bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        except TelegramAPIError as exc:
            logging.error("Failed to send notification to chat %s: %s", chat_id, exc)
        except Exception as exc:  # pragma: no cover
            logging.exception("Unexpected error while sending notification to chat %s: %s", chat_id, exc)

    async def fetch_leads(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, created_at, scenario, name, use_case, custom_task, extra,
                   telegram_id, username, first_name, last_name, language_code, is_premium, is_bot, user_json,
                   vps_ip, ssh_ok, root_password, domain, protocols, bot_token,
                   payment_id, payment_url, payment_status, status
            FROM leads
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def fetch_all_leads(self) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        cursor = await self._db.execute(
            """
            SELECT id, created_at, scenario, name, use_case, custom_task, extra,
                   telegram_id, username, first_name, last_name, language_code, is_premium, is_bot, user_json,
                   vps_ip, ssh_ok, root_password, domain, protocols, bot_token,
                   payment_id, payment_url, payment_status, status
            FROM leads
            ORDER BY id DESC
            """
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

    async def count_users(self) -> int:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        cursor = await self._db.execute("SELECT COUNT(*) as cnt FROM users")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["cnt"] if row and "cnt" in row.keys() else 0)

    async def fetch_users(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        cursor = await self._db.execute(
            """
            SELECT u.*, l.id as lead_id, l.status as lead_status
            FROM users u
            LEFT JOIN leads l ON l.telegram_id = u.telegram_id
            ORDER BY u.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def get_user(self, user_id: int) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        cursor = await self._db.execute(
            """
            SELECT u.*, l.id as lead_id, l.status as lead_status
            FROM users u
            LEFT JOIN leads l ON l.telegram_id = u.telegram_id
            WHERE u.id = ?
            LIMIT 1
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def get_user_by_telegram(self, telegram_id: str) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        cursor = await self._db.execute(
            """
            SELECT u.*, l.id as lead_id, l.status as lead_status
            FROM users u
            LEFT JOIN leads l ON l.telegram_id = u.telegram_id
            WHERE u.telegram_id = ?
            LIMIT 1
            """,
            (telegram_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def delete_user(self, user_id: int) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        user = await self.get_user(user_id)
        if not user:
            return None
        tgid = user.get("telegram_id")
        lead = None
        if tgid:
            lead = await self.get_lead_by_telegram(str(tgid))
            await self._db.execute("DELETE FROM leads WHERE telegram_id=?", (str(tgid),))
            self._lead_cache.discard(str(tgid))
        await self._db.execute("DELETE FROM users WHERE id=?", (user_id,))
        await self._db.commit()
        if lead:
            user["lead"] = lead
        return user

    async def search_users(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        query = query.strip().lstrip("@")
        params: list[Any] = []
        sql = """
        SELECT u.*, l.id as lead_id, l.status as lead_status
        FROM users u
        LEFT JOIN leads l ON l.telegram_id = u.telegram_id
        WHERE 1=1
        """
        if query.isdigit():
            sql += " AND (u.telegram_id = ? OR u.id = ?)"
            params.extend([query, int(query)])
        else:
            sql += " AND (LOWER(u.username) LIKE ? OR LOWER(u.first_name) LIKE ? OR LOWER(u.last_name) LIKE ?)"
            like = f"%{query.lower()}%"
            params.extend([like, like, like])
        sql += " ORDER BY u.id DESC LIMIT ?"
        params.append(limit)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def has_lead(self, telegram_id: int) -> bool:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        tgid = str(telegram_id)
        if tgid in self._lead_cache:
            return True
        cursor = await self._db.execute(
            "SELECT 1 FROM leads WHERE telegram_id = ? LIMIT 1",
            (tgid,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row:
            self._lead_cache.add(tgid)
            return True
        return False

    async def get_lead(self, lead_id: int) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        cursor = await self._db.execute("SELECT * FROM leads WHERE id=?", (lead_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def get_lead_by_telegram(self, telegram_id: str) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        cursor = await self._db.execute("SELECT * FROM leads WHERE telegram_id=? LIMIT 1", (telegram_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def update_lead_by_telegram(self, telegram_id: str, updates: Dict[str, Any]) -> Optional[dict[str, Any]]:
        existing = await self.get_lead_by_telegram(telegram_id)
        if not existing:
            return None
        merged = {**existing, **updates, "telegram_id": telegram_id}
        protocols = merged.get("protocols")
        if isinstance(protocols, str):
            try:
                merged["protocols"] = json.loads(protocols)
            except Exception:
                merged["protocols"] = [protocols]
        await self._save_user_from_payload(merged)
        await self._save_lead(merged)
        return await self.get_lead_by_telegram(telegram_id)

    async def delete_lead(self, lead_id: int) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        lead = await self.get_lead(lead_id)
        if not lead:
            return None
        await self._db.execute("DELETE FROM leads WHERE id=?", (lead_id,))
        await self._db.commit()
        if lead.get("telegram_id"):
            self._lead_cache.discard(str(lead["telegram_id"]))
        return lead

    async def search_leads(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        query = query.strip().lstrip("@")
        params = []
        sql = """
        SELECT * FROM leads
        WHERE 1=1
        """
        if query.isdigit():
            sql += " AND (telegram_id = ? OR id = ?)"
            params.extend([query, int(query)])
        else:
            sql += " AND (LOWER(username) LIKE ?)"
            params.append(f"%{query.lower()}%")
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def update_status(self, lead_id: int, status: str) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        await self._db.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))
        await self._db.commit()
        return await self.get_lead(lead_id)

    async def send_user_notification(self, telegram_id: int, text: str) -> bool:
        if not self._main_bot:
            return False
        try:
            await self._main_bot.send_message(chat_id=int(telegram_id), text=text)
            return True
        except Exception as exc:  # pragma: no cover
            logging.error("Failed to notify user %s: %s", telegram_id, exc)
            return False

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
        first_name = html.escape(payload.get("first_name") or "—")
        last_name = html.escape(payload.get("last_name") or "—")
        language_code = html.escape(payload.get("language_code") or "—")
        premium_display = "Да" if payload.get("is_premium") else "Нет" if payload.get("is_premium") is not None else "—"
        bot_flag_display = "Да" if payload.get("is_bot") else "Нет" if payload.get("is_bot") is not None else "—"
        vps_ip = html.escape(payload.get("vps_ip") or "—")
        domain = html.escape(payload.get("domain") or "—")
        ssh_ok = payload.get("ssh_ok")
        ssh_status = "Да" if ssh_ok else ("Нет" if ssh_ok is not None else "—")
        root_password = html.escape(payload.get("root_password") or "—")
        protocols = payload.get("protocols")
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
        status = html.escape(payload.get("status") or "—")

        lines = [
            "<b>Новая/обновленная заявка</b>",
            f"Сценарий: <b>{scenario}</b>",
            f"Имя: <b>{name}</b>",
            f"Кейс: <b>{use_case}</b>",
            f"Своя задача: {custom_task}",
            f"Доп. информация: {extra}",
            f"Telegram ID: <code>{user_id}</code>",
            f"Username: {username_display}",
            f"Имя в TG: <b>{first_name}</b>",
            f"Фамилия в TG: <b>{last_name}</b>",
            f"Язык: <b>{language_code}</b>",
            f"Premium: {premium_display}",
            f"User is bot: {bot_flag_display}",
            "",
            f"VPS IP: {vps_ip}",
            f"Домен: {domain}",
            f"SSH доступ: {ssh_status}",
            f"Root пароль: <code>{root_password}</code>",
            f"Протоколы: {protocols_display}",
            f"Bot Token: {bot_token_masked}",
            f"Платеж: {payment_status}",
            f"Payment URL: {payment_url}",
            f"Статус: {status}",
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
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                is_premium INTEGER,
                is_bot INTEGER,
                user_json TEXT,
                vps_ip TEXT,
                ssh_ok INTEGER,
                root_password TEXT,
                domain TEXT,
                protocols TEXT,
                bot_token TEXT,
                payment_id TEXT,
                payment_url TEXT,
                payment_status TEXT,
                status TEXT
            );
            """
        )
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
            ("status", "ALTER TABLE leads ADD COLUMN status TEXT"),
            ("first_name", "ALTER TABLE leads ADD COLUMN first_name TEXT"),
            ("last_name", "ALTER TABLE leads ADD COLUMN last_name TEXT"),
            ("language_code", "ALTER TABLE leads ADD COLUMN language_code TEXT"),
            ("is_premium", "ALTER TABLE leads ADD COLUMN is_premium INTEGER"),
            ("is_bot", "ALTER TABLE leads ADD COLUMN is_bot INTEGER"),
            ("user_json", "ALTER TABLE leads ADD COLUMN user_json TEXT"),
        ]:
            if not await self._has_column(column, table="leads"):
                await self._db.execute(ddl)

        # Ensure unique index without partial condition for UPSERT compatibility.
        await self._drop_index_if_exists("idx_leads_telegram_id")
        await self._drop_index_if_exists("idx_leads_telegram_id_partial")
        await self._db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_telegram_id
            ON leads(telegram_id);
            """
        )

        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                telegram_id TEXT NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                is_premium INTEGER,
                is_bot INTEGER,
                raw_data TEXT
            );
            """
        )
        for column, ddl in [
            ("raw_data", "ALTER TABLE users ADD COLUMN raw_data TEXT"),
            ("language_code", "ALTER TABLE users ADD COLUMN language_code TEXT"),
            ("is_premium", "ALTER TABLE users ADD COLUMN is_premium INTEGER"),
            ("is_bot", "ALTER TABLE users ADD COLUMN is_bot INTEGER"),
        ]:
            if not await self._has_column(column, table="users"):
                await self._db.execute(ddl)

        await self._db.commit()

    async def _has_column(self, name: str, table: str = "leads") -> bool:
        cursor = await self._db.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        await cursor.close()
        return any(row[1] == name for row in rows)

    async def _drop_index_if_exists(self, name: str) -> None:
        cursor = await self._db.execute("PRAGMA index_list(leads)")
        indexes = [row[1] for row in await cursor.fetchall()]
        await cursor.close()
        if name in indexes:
            await self._db.execute(f"DROP INDEX {name}")

    async def _save_lead(self, payload: Dict[str, Any]) -> None:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")

        protocols = payload.get("protocols")
        protocols_json = json.dumps(protocols, ensure_ascii=False) if protocols is not None else None
        status = payload.get("status")
        existing = None
        if payload.get("telegram_id"):
            existing = await self.get_lead_by_telegram(str(payload.get("telegram_id")))
        if not status:
            status = (existing or {}).get("status") or "Принята"

        def _bool(val: Any) -> Optional[int]:
            if val is None:
                return None
            return 1 if bool(val) else 0

        await self._db.execute(
            """
            INSERT INTO leads
            (telegram_id, created_at, scenario, name, use_case, custom_task, extra, username,
             first_name, last_name, language_code, is_premium, is_bot, user_json,
             vps_ip, ssh_ok, root_password, domain, protocols, bot_token,
             payment_id, payment_url, payment_status, status)
            VALUES (?, COALESCE((SELECT created_at FROM leads WHERE telegram_id=?), CURRENT_TIMESTAMP),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                scenario=COALESCE(excluded.scenario, leads.scenario),
                name=COALESCE(excluded.name, leads.name),
                use_case=COALESCE(excluded.use_case, leads.use_case),
                custom_task=COALESCE(excluded.custom_task, leads.custom_task),
                extra=COALESCE(excluded.extra, leads.extra),
                username=COALESCE(excluded.username, leads.username),
                first_name=COALESCE(excluded.first_name, leads.first_name),
                last_name=COALESCE(excluded.last_name, leads.last_name),
                language_code=COALESCE(excluded.language_code, leads.language_code),
                is_premium=COALESCE(excluded.is_premium, leads.is_premium),
                is_bot=COALESCE(excluded.is_bot, leads.is_bot),
                user_json=COALESCE(excluded.user_json, leads.user_json),
                vps_ip=COALESCE(excluded.vps_ip, leads.vps_ip),
                ssh_ok=COALESCE(excluded.ssh_ok, leads.ssh_ok),
                root_password=COALESCE(excluded.root_password, leads.root_password),
                domain=COALESCE(excluded.domain, leads.domain),
                protocols=COALESCE(excluded.protocols, leads.protocols),
                bot_token=COALESCE(excluded.bot_token, leads.bot_token),
                payment_id=COALESCE(excluded.payment_id, leads.payment_id),
                payment_url=COALESCE(excluded.payment_url, leads.payment_url),
                payment_status=COALESCE(excluded.payment_status, leads.payment_status),
                status=COALESCE(excluded.status, leads.status);
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
                payload.get("first_name"),
                payload.get("last_name"),
                payload.get("language_code"),
                _bool(payload.get("is_premium")),
                _bool(payload.get("is_bot")),
                payload.get("user_json"),
                payload.get("vps_ip"),
                _bool(payload.get("ssh_ok")),
                payload.get("root_password"),
                payload.get("domain"),
                protocols_json,
                payload.get("bot_token"),
                payload.get("payment_id"),
                payload.get("payment_url"),
                payload.get("payment_status"),
                status,
            ),
        )
        await self._db.commit()
        if payload.get("telegram_id"):
            self._lead_cache.add(str(payload["telegram_id"]))

    async def _save_user_from_payload(self, payload: Dict[str, Any]) -> None:
        if not self._db:
            raise RuntimeError("NotificationService DB is not initialized")
        telegram_id = payload.get("telegram_id")
        if not telegram_id:
            return
        raw_json = payload.get("user_json")
        if not raw_json and payload.get("raw_user"):
            try:
                raw_json = json.dumps(payload["raw_user"], ensure_ascii=False)
            except Exception:
                raw_json = None

        def _bool(val: Any) -> Optional[int]:
            if val is None:
                return None
            return 1 if bool(val) else 0

        await self._db.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, last_name, language_code, is_premium, is_bot, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=COALESCE(excluded.username, users.username),
                first_name=COALESCE(excluded.first_name, users.first_name),
                last_name=COALESCE(excluded.last_name, users.last_name),
                language_code=COALESCE(excluded.language_code, users.language_code),
                is_premium=COALESCE(excluded.is_premium, users.is_premium),
                is_bot=COALESCE(excluded.is_bot, users.is_bot),
                raw_data=COALESCE(excluded.raw_data, users.raw_data);
            """,
            (
                str(telegram_id),
                payload.get("username"),
                payload.get("first_name"),
                payload.get("last_name"),
                payload.get("language_code"),
                _bool(payload.get("is_premium")),
                _bool(payload.get("is_bot")),
                raw_json,
            ),
        )
        await self._db.commit()

    async def _warm_cache(self) -> None:
        if not self._db:
            return
        cursor = await self._db.execute("SELECT telegram_id FROM leads WHERE telegram_id IS NOT NULL")
        rows = await cursor.fetchall()
        await cursor.close()
        self._lead_cache = {str(r["telegram_id"]) for r in rows if r["telegram_id"]}


__all__ = ["NotificationService"]
