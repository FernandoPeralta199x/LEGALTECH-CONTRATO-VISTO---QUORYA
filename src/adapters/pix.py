# src/adapters/pix.py
"""Seam Pix (serverless), provider-agnóstico. Mock agora; provider real na Fase 7 (env).
Espelha src/adapters/payment.py. NADA de credencial/rede real no mock — o MockPixProvider
gera cobrança/QR/copia-e-cola FALSOS e assina o webhook com um segredo de dev, permitindo
E2E de confirmação assíncrona (PENDING_PAYMENT -> webhook -> PAID) sem gateway real."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from src.services.pix.states import PENDING_PAYMENT

# TTL padrão do QR dinâmico (o provider real pode sobrescrever via order.expires_in_seconds).
DEFAULT_PIX_TTL_SECONDS = 15 * 60


class PixSignatureError(ValueError):
    """Assinatura de webhook Pix inválida — rejeitar (401) SEM tocar no banco."""


@dataclass(frozen=True)
class PixOrder:
    """Entrada para criar a cobrança. O backend deriva amount/org/case (nunca do cliente)."""
    amount_cents: int
    case_reference: str
    organization_id: str
    idempotency_key: str
    currency: str = "BRL"
    expires_in_seconds: int = DEFAULT_PIX_TTL_SECONDS


@dataclass(frozen=True)
class PixCharge:
    """Retorno de create_dynamic_charge — SÓ campos exibíveis (contrato estrito do spec §3)."""
    payment_id: str            # id/txid da cobrança no provider
    pix_copy_paste: str        # BR Code EMV "copia e cola"
    expires_at: str            # ISO-8601 UTC
    status: str                # PENDING_PAYMENT no happy path
    qr_code_image: str | None = None  # data-URI/URL do QR (do provider real); None => o FE renderiza do copia_cola

    def to_public(self, order_id: str) -> dict:
        """Contrato estrito ao FE — apenas os 6 campos do spec §3."""
        return {
            "orderId": order_id,
            "paymentId": self.payment_id,
            "qrCodeImage": self.qr_code_image,
            "pixCopyPaste": self.pix_copy_paste,
            "expiresAt": self.expires_at,
            "status": self.status,
        }


@dataclass(frozen=True)
class PixWebhookEvent:
    """Evento de webhook normalizado, provider-agnóstico (para dedup + cross-check de valor)."""
    event_id: str              # id único do evento no provider (chave de dedup)
    payment_id: str
    status: str                # PAID | EXPIRED | PAYMENT_FAILED | REFUNDED
    amount_cents: int          # p/ validar contra o esperado na cobrança
    currency: str = "BRL"


@runtime_checkable
class PixProvider(Protocol):
    def create_dynamic_charge(self, order: PixOrder) -> PixCharge: ...
    def handle_webhook(self, payload: bytes, signature: str) -> PixWebhookEvent: ...
    def get_status(self, payment_id: str) -> PixWebhookEvent: ...


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _mock_br_code(payment_id: str, amount_cents: int) -> str:
    """BR Code de SIMULAÇÃO (NÃO pagável) — formato EMV-like só para exercitar a UI/fluxo."""
    reais = f"{amount_cents / 100:.2f}"
    return f"000201...MOCK-PIX-{payment_id}-BRL{reais}...6304MOCK"


class MockPixProvider:
    """Provider de desenvolvimento. Assina/valida o webhook com HMAC-SHA256 e um segredo de
    dev — não é segurança real, mas exercita o caminho de verificação de assinatura."""

    def __init__(self, webhook_secret: str | None = None,
                 ttl_seconds: int = DEFAULT_PIX_TTL_SECONDS) -> None:
        self._secret = (
            webhook_secret or os.getenv("PIX_WEBHOOK_SECRET") or "dev-pix-mock-secret"
        ).encode()
        self._ttl = ttl_seconds

    def create_dynamic_charge(self, order: PixOrder) -> PixCharge:
        payment_id = f"mock_pix_{order.case_reference}_{uuid.uuid4().hex[:8]}"
        ttl = order.expires_in_seconds or self._ttl
        expires_at = (_now_utc() + timedelta(seconds=ttl)).isoformat()
        return PixCharge(
            payment_id=payment_id,
            pix_copy_paste=_mock_br_code(payment_id, order.amount_cents),
            expires_at=expires_at,
            status=PENDING_PAYMENT,
            qr_code_image=None,  # mock não gera imagem; o FE renderiza do copia-e-cola
        )

    def sign(self, payload: bytes) -> str:
        """Assinatura HMAC-SHA256 (hex) — dev-only, simula o header assinado do provider."""
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def handle_webhook(self, payload: bytes, signature: str) -> PixWebhookEvent:
        # Verificação de assinatura ANTES de qualquer parse de negócio (constant-time).
        expected = self.sign(payload)
        if not hmac.compare_digest(expected, signature or ""):
            raise PixSignatureError("Assinatura de webhook inválida")
        data = json.loads(payload.decode())
        return PixWebhookEvent(
            event_id=str(data["event_id"]),
            payment_id=str(data["payment_id"]),
            status=str(data["status"]),
            amount_cents=int(data["amount_cents"]),
            currency=str(data.get("currency", "BRL")),
        )

    def get_status(self, payment_id: str) -> PixWebhookEvent:
        # No mock não há estado no "provider": o backend é a fonte da verdade. get_status
        # é a rede de segurança (na Fase 7 consulta o provider real). No dev devolve
        # PENDING_PAYMENT — o avanço para PAID vem do webhook mock disparado explicitamente.
        return PixWebhookEvent(
            event_id=f"status_{payment_id}", payment_id=payment_id,
            status=PENDING_PAYMENT, amount_cents=0,
        )


class RealPixProvider:
    """Placeholder — provider Pix real (Asaas / Mercado Pago / ...) na Fase 7.
    Requer credenciais (PAYMENT_API_KEY) e segredo de webhook (Secrets Manager)."""

    def __init__(self, provider: str, mode: str, api_key: str,
                 webhook_secret: str | None) -> None:
        self._provider, self._mode = provider, mode
        self._api_key, self._secret = api_key, webhook_secret

    def create_dynamic_charge(self, order: PixOrder) -> PixCharge:
        raise NotImplementedError(
            "RealPixProvider não implementado — aguardando provider Pix (Fase 7).")

    def handle_webhook(self, payload: bytes, signature: str) -> PixWebhookEvent:
        raise NotImplementedError(
            "RealPixProvider não implementado — aguardando provider Pix (Fase 7).")

    def get_status(self, payment_id: str) -> PixWebhookEvent:
        raise NotImplementedError(
            "RealPixProvider não implementado — aguardando provider Pix (Fase 7).")


def create_pix_provider(provider: str | None = None, mode: str | None = None,
                        api_key: str | None = None,
                        webhook_secret: str | None = None) -> PixProvider:
    """Factory por env (espelha create_payment_provider). Default sem env vars => mock."""
    provider = provider or os.getenv("PIX_PROVIDER", "mock")
    mode = mode or os.getenv("PAYMENT_MODE", "mock")
    api_key = api_key or os.getenv("PAYMENT_API_KEY")
    webhook_secret = webhook_secret or os.getenv("PIX_WEBHOOK_SECRET")
    if provider == "mock" or mode == "mock":
        return MockPixProvider(webhook_secret=webhook_secret)
    if not api_key:
        raise ValueError("PAYMENT_API_KEY obrigatória para provider Pix real")
    return RealPixProvider(provider=provider, mode=mode, api_key=api_key,
                           webhook_secret=webhook_secret)
