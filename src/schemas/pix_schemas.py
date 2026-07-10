"""Schemas do módulo Pix (prepare). Corpo estrito — o backend deriva amount/org/case
(nunca do cliente). Pix é sempre à vista (1x): o corpo só carrega a chave de idempotência."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PixChargeRequestSchema(BaseModel):
    """Corpo do POST /cases/{caseId}/pix-charge. O valor (1x) é recomputado server-side
    e a org vem do JWT — o cliente NÃO envia valor nem método (Pix implícito)."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=255)
