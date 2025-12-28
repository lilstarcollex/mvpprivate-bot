from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class PaymentResult:
    payment_id: str
    payment_url: str
    status: str
    raw: Dict[str, Any]


class PaymentClient:
    """
    Simple wrapper for YooKassa-like payments.
    For now works in stub mode if credentials are absent.
    """

    def __init__(
        self,
        shop_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        return_url: Optional[str] = None,
    ) -> None:
        self.shop_id = shop_id
        self.secret_key = secret_key
        self.return_url = return_url or "https://t.me/"

    @property
    def enabled(self) -> bool:
        return bool(self.shop_id and self.secret_key)

    async def create_payment(self, amount_rub: int, description: str, metadata: Optional[Dict[str, Any]] = None) -> PaymentResult:
        # Stubbed payment generation; replace with real YooKassa SDK/HTTP when keys provided.
        payment_id = str(uuid.uuid4())
        payment_url = f"https://pay.yookassa.ru/demo?order_id={payment_id}"
        status = "pending_stub"
        raw = {
            "description": description,
            "amount": amount_rub,
            "metadata": metadata or {},
            "return_url": self.return_url,
            "enabled": self.enabled,
        }
        return PaymentResult(payment_id=payment_id, payment_url=payment_url, status=status, raw=raw)
