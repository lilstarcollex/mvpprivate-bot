from __future__ import annotations

import asyncio
import re
import socket
from typing import Optional, Tuple

IP_PATTERN = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}")


def extract_ipv4(text: str) -> Optional[str]:
    """Return first valid IPv4 address found in text."""
    for match in IP_PATTERN.finditer(text):
        candidate = match.group(0)
        parts = candidate.split(".")
        if all(0 <= int(p) <= 255 for p in parts):
            return candidate
    return None


async def resolve_domain(domain: str) -> Optional[str]:
    """Resolve domain to IPv4, returns first address or None."""
    domain = domain.strip()
    if not domain:
        return None

    def _resolve() -> Optional[str]:
        try:
            info = socket.gethostbyname_ex(domain)
            if info and info[2]:
                return info[2][0]
        except socket.gaierror:
            return None
        return None

    return await asyncio.to_thread(_resolve)
