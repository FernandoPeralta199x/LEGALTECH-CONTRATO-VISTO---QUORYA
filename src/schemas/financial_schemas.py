"""Schemas Pydantic do módulo Financeiro (Fase 4: custos de API externa).

O TOTAL do custo é calculado pelo BANCO (coluna GENERATED em external_api_costs);
o cliente NUNCA envia total_cost_cents — `extra="forbid"` rejeita esse campo e
quaisquer outros estranhos, tornando a forja impossível.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Espelham os CHECKs da migration 018.
MAX_UNIT_COST_CENTS = 100_000_000   # R$ 1.000.000,00 por unidade
MAX_QUANTITY = 1_000_000

_ALLOWED_STATUS = ("previsto", "processado")


class CreateApiCostRequest(BaseModel):
    """Corpo do POST /financial/api-costs (registro manual de gasto de API)."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    operation: str = Field(min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY)
    unit_cost_cents: int = Field(ge=0, le=MAX_UNIT_COST_CENTS)
    status: str = Field(default="processado")
    # ISO date/datetime; default now() no banco se ausente.
    incurred_at: Optional[str] = Field(default=None, max_length=40)
    request_id: Optional[str] = Field(default=None, max_length=64)
    case_id: Optional[str] = Field(default=None, max_length=64)
    client_id: Optional[str] = Field(default=None, max_length=64)
    external_id: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("provider", "operation")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("não pode ser vazio")
        return v

    @field_validator("status")
    @classmethod
    def _status_ok(cls, v: str) -> str:
        if v not in _ALLOWED_STATUS:
            raise ValueError("deve ser 'previsto' ou 'processado'")
        return v
