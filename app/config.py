from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    main_bot_token: str
    notification_bot_token: str
    notification_chat_ids: list[int]
    storage_path: Path
    leads_db_path: Path
    yookassa_shop_id: str | None
    yookassa_secret_key: str | None
    yookassa_return_url: str | None


def load_config(env_path: str = ".env") -> Config:
    """Load configuration from environment or .env file."""
    load_dotenv(env_path)

    main_bot_token = os.getenv("MAIN_BOT_TOKEN")
    notification_bot_token = os.getenv("NOTIFICATION_BOT_TOKEN")
    notification_chat_ids_raw = os.getenv("NOTIFICATION_CHAT_ID")
    storage_path = Path(os.getenv("STORAGE_PATH", "data/state.sqlite3"))
    leads_db_path = Path(os.getenv("LEADS_DB_PATH", "data/leads.sqlite3"))
    yookassa_shop_id = os.getenv("YOOKASSA_SHOP_ID")
    yookassa_secret_key = os.getenv("YOOKASSA_SECRET_KEY")
    yookassa_return_url = os.getenv("YOOKASSA_RETURN_URL", "https://t.me/")

    if not main_bot_token:
        raise ValueError("MAIN_BOT_TOKEN is required")
    if not notification_bot_token:
        raise ValueError("NOTIFICATION_BOT_TOKEN is required")
    if not notification_chat_ids_raw:
        raise ValueError("NOTIFICATION_CHAT_ID is required")

    notification_chat_ids = _parse_chat_ids(notification_chat_ids_raw)

    storage_path.parent.mkdir(parents=True, exist_ok=True)
    leads_db_path.parent.mkdir(parents=True, exist_ok=True)

    return Config(
        main_bot_token=main_bot_token,
        notification_bot_token=notification_bot_token,
        notification_chat_ids=notification_chat_ids,
        storage_path=storage_path,
        leads_db_path=leads_db_path,
        yookassa_shop_id=yookassa_shop_id,
        yookassa_secret_key=yookassa_secret_key,
        yookassa_return_url=yookassa_return_url,
    )


def _parse_chat_ids(raw_value: str) -> list[int]:
    """Parse comma/space separated chat IDs from env value."""
    parts = [p for p in re.split(r"[\\s,]+", raw_value) if p]
    if not parts:
        raise ValueError("NOTIFICATION_CHAT_ID is required")
    chat_ids: list[int] = []
    for part in parts:
        try:
            chat_ids.append(int(part))
        except ValueError as exc:
            raise ValueError("NOTIFICATION_CHAT_ID must contain only integer chat IDs (comma or space separated)") from exc
    return chat_ids


__all__ = ["Config", "load_config"]
