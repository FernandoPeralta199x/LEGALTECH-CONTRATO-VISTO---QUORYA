# src/adapters/payment.py
"""Seam de pagamento (serverless). Mock simulado agora; Real placeholder para gateway (env).
Espelha o padrão de src/adapters/procon.py."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol, runtime_checkable

Method = Literal["pix", "boleto", "cartao"]
Mode = Literal["mock", "sandbox", "live"]
Status = Literal["simulated", "pending", "paid", "failed", "canceled", "expired", "refunded"]


@dataclass(frozen=True)
class PaymentRequest:
    amount_cents: int
    installments: int
    method: Method
    case_reference: str
    organization_id: str
    idempotency_key: str
    schedule: list[dict]
    currency: str = "BRL"
    mode: Mode = "mock"


@dataclass(frozen=True)
class PaymentResult:
    provider: str
    mode: Mode
    status: Status
    method: Method
    external_reference: str | None
    payment_form: dict = field(default_factory=dict)
    requested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw: dict = field(default_factory=dict)

    def to_public(self) -> dict:
        return {"provider": self.provider, "mode": self.mode, "status": self.status,
                "method": self.method, "external_reference": self.external_reference,
                "payment_form": self.payment_form, "requested_at": self.requested_at}


@runtime_checkable
class PaymentProvider(Protocol):
    def create_charge(self, req: PaymentRequest) -> PaymentResult: ...


def _mock_form(method: Method) -> dict:
    if method == "pix":
        return {"type": "pix", "qr_code": "MOCK-PIX-QR", "copia_cola": "000201MOCK"}
    if method == "boleto":
        return {"type": "boleto", "url": "https://mock/boleto", "linha_digitavel": "00000.00000"}
    return {"type": "cartao", "authorization_code": "MOCK-AUTH-123"}


class MockPaymentProvider:
    def create_charge(self, req: PaymentRequest) -> PaymentResult:
        return PaymentResult(
            provider="mock", mode="mock", status="simulated", method=req.method,
            external_reference=f"mock_{req.case_reference}_{uuid.uuid4().hex[:8]}",
            payment_form=_mock_form(req.method))


class RealPaymentProvider:
    """Placeholder — implementação real na AWS (sandbox/live). Requer PAYMENT_API_KEY."""
    def __init__(self, provider: str, mode: Mode, api_key: str) -> None:
        self._provider, self._mode, self._api_key = provider, mode, api_key

    def create_charge(self, req: PaymentRequest) -> PaymentResult:
        raise NotImplementedError(
            "RealPaymentProvider não implementado — aguardando gateway (impl. AWS).")


def create_payment_provider(provider: str | None = None, mode: str | None = None,
                            api_key: str | None = None) -> PaymentProvider:
    provider = provider or os.getenv("PAYMENT_PROVIDER", "mock")
    mode = mode or os.getenv("PAYMENT_MODE", "mock")
    api_key = api_key or os.getenv("PAYMENT_API_KEY")
    if provider == "mock" or mode == "mock":
        return MockPaymentProvider()
    if not api_key:
        raise ValueError("PAYMENT_API_KEY obrigatória para provider real")
    return RealPaymentProvider(provider=provider, mode=mode, api_key=api_key)  # type: ignore[arg-type]
