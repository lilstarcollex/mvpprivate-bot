from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    """SQLite FSM storage to persist states/data without external services."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
        """Open connection and ensure table exists."""
        if self._conn:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fsm_data (
                bot_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL DEFAULT -1,
                business_connection_id TEXT NOT NULL DEFAULT '',
                destiny TEXT NOT NULL,
                state TEXT,
                data TEXT,
                PRIMARY KEY (bot_id, chat_id, user_id, thread_id, business_connection_id, destiny)
            );
            """
        )
        # Normalize existing nullable values and drop duplicates that may appear when NULLs were used.
        await self._conn.execute(
            """
            DELETE FROM fsm_data
            WHERE rowid NOT IN (
                SELECT MIN(rowid)
                FROM fsm_data
                GROUP BY bot_id, chat_id, user_id, COALESCE(thread_id, -1), COALESCE(business_connection_id, ''), destiny
            );
            """
        )
        await self._conn.execute(
            """
            UPDATE fsm_data
            SET thread_id = COALESCE(thread_id, -1),
                business_connection_id = COALESCE(business_connection_id, '')
            WHERE thread_id IS NULL OR business_connection_id IS NULL;
            """
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        state_value = state.state if isinstance(state, State) else state
        data = await self.get_data(key)
        await self._upsert(key, state=state_value, data=data)

    async def get_state(self, key: StorageKey) -> str | None:
        row = await self._fetch_row(key)
        if not row:
            return None
        return row["state"]

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        if not isinstance(data, dict):
            msg = f"Data must be a dict or dict-like object, got {type(data).__name__}"
            raise TypeError(msg)
        state = await self.get_state(key)
        await self._upsert(key, state=state, data=data)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await self._fetch_row(key)
        if not row or row["data"] is None:
            return {}
        try:
            return json.loads(row["data"])
        except json.JSONDecodeError:
            return {}

    # Internal helpers
    async def _upsert(self, key: StorageKey, state: StateType = None, data: Mapping[str, Any] | None = None) -> None:
        if not self._conn:
            await self.start()
        assert self._conn

        key_values = self._normalize_key(key)
        data_json = json.dumps(dict(data)) if data is not None else None
        await self._conn.execute(
            """
            INSERT INTO fsm_data (bot_id, chat_id, user_id, thread_id, business_connection_id, destiny, state, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bot_id, chat_id, user_id, thread_id, business_connection_id, destiny)
            DO UPDATE SET
                state=excluded.state,
                data=COALESCE(excluded.data, fsm_data.data);
            """,
            (
                key_values["bot_id"],
                key_values["chat_id"],
                key_values["user_id"],
                key_values["thread_id"],
                key_values["business_connection_id"],
                key_values["destiny"],
                state,
                data_json,
            ),
        )
        await self._conn.commit()

    async def _fetch_row(self, key: StorageKey) -> Optional[aiosqlite.Row]:
        if not self._conn:
            await self.start()
        assert self._conn
        self._conn.row_factory = aiosqlite.Row
        key_values = self._normalize_key(key)
        cursor = await self._conn.execute(
            """
            SELECT state, data
            FROM fsm_data
            WHERE bot_id=? AND chat_id=? AND user_id=? AND thread_id=?
                  AND business_connection_id=? AND destiny=?;
            """,
            (
                key_values["bot_id"],
                key_values["chat_id"],
                key_values["user_id"],
                key_values["thread_id"],
                key_values["business_connection_id"],
                key_values["destiny"],
            ),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    @staticmethod
    def _normalize_key(key: StorageKey) -> dict[str, Any]:
        return {
            "bot_id": key.bot_id,
            "chat_id": key.chat_id,
            "user_id": key.user_id,
            "thread_id": key.thread_id if key.thread_id is not None else -1,
            "business_connection_id": key.business_connection_id or "",
            "destiny": key.destiny,
        }


__all__ = ["SQLiteStorage"]
