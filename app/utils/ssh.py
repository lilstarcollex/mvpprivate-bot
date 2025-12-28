from __future__ import annotations

import asyncio
from typing import Optional

import paramiko


async def test_ssh_connection(host: str, password: str, port: int = 22, timeout: int = 10) -> bool:
    """Try SSH connect as root with password. Returns True on success."""

    def _connect() -> bool:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host,
                port=port,
                username="root",
                password=password,
                timeout=timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            return True
        except Exception:
            return False
        finally:
            try:
                client.close()
            except Exception:
                pass

    return await asyncio.to_thread(_connect)
