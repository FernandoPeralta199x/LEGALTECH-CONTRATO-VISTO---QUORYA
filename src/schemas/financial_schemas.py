"""Schemas Pydantic do módulo Financeiro (Fase 4: custos de API externa).

O TOTAL do custo é calculado pelo BANCO (coluna GENERATED em external_api_costs);
o cliente NUNCA envia total_cost_cents — `extra="forbid"` rejeita esse campo e
quaisquer outros estranhos, tornando a forja impossível.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


# ── Fase 5: Tributos e Notas (controle interno; sem cálculo/emissão fiscal) ──
MAX_TAX_CENTS = 100_000_000_000            # espelha o CHECK bigint da migration 019
MAX_ISSUANCE_COST_CENTS = 100_000_000

_TAX_TYPES = ("iss", "pis", "cofins", "irpj", "csll", "inss", "icms", "das", "outro")
_TAX_STATUS = ("estimado", "confirmado", "divergente", "cancelado", "nao_aplicavel")
_DOC_TYPES = ("nfse", "nfe", "nfce", "recibo", "outro")
_DOC_STATUS = ("not_required", "note_pending", "authorized", "rejected", "note_canceled")


def _safe_pointer(v: Optional[str]) -> Optional[str]:
    """URL/XML só como ponteiro seguro (https/s3) — bloqueia javascript:/data:."""
    if v is None:
        return v
    v = v.strip()
    if not v:
        return None
    low = v.lower()
    if not (low.startswith("https://") or low.startswith("s3://")):
        raise ValueError("deve ser uma URL https:// ou s3://")
    return v


class CreateTaxProvisionRequest(BaseModel):
    """POST /financial/taxes — registro manual de tributo (informativo). O valor é
    DIGITADO pelo admin/contador; o sistema NÃO deriva tributo de alíquota."""

    model_config = ConfigDict(extra="forbid")

    tax_type: str
    base_amount_cents: int = Field(default=0, ge=0, le=MAX_TAX_CENTS)
    rate_bps: Optional[int] = Field(default=None, ge=0, le=10000)   # alíquota informativa
    estimated_amount_cents: int = Field(default=0, ge=0, le=MAX_TAX_CENTS)
    confirmed_amount_cents: Optional[int] = Field(default=None, ge=0, le=MAX_TAX_CENTS)
    status: str = Field(default="estimado")
    competencia_ano: int = Field(ge=2000, le=2100)
    competencia_mes: int = Field(ge=1, le=12)
    request_id: Optional[str] = Field(default=None, max_length=64)
    case_id: Optional[str] = Field(default=None, max_length=64)
    client_id: Optional[str] = Field(default=None, max_length=64)
    provider: Optional[str] = Field(default=None, max_length=80)
    external_id: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("tax_type")
    @classmethod
    def _tax_type_ok(cls, v: str) -> str:
        if v not in _TAX_TYPES:
            raise ValueError("tipo de tributo inválido")
        return v

    @field_validator("status")
    @classmethod
    def _tax_status_ok(cls, v: str) -> str:
        if v not in _TAX_STATUS:
            raise ValueError("status de tributo inválido")
        return v

    @model_validator(mode="after")
    def _confirmed_required(self):
        if self.status in ("confirmado", "divergente") and self.confirmed_amount_cents is None:
            raise ValueError("valor confirmado é obrigatório para status 'confirmado'/'divergente'")
        return self


class CreateFiscalDocumentRequest(BaseModel):
    """POST /financial/fiscal-documents — REGISTRA uma nota já emitida (não emite).
    O sistema nunca gera número fiscal; o admin transcreve o número real."""

    model_config = ConfigDict(extra="forbid")

    doc_type: str
    numero: Optional[str] = Field(default=None, max_length=60)
    serie: Optional[str] = Field(default=None, max_length=20)
    codigo_verificacao: Optional[str] = Field(default=None, max_length=120)
    status: str = Field(default="note_pending")
    issued_at: Optional[str] = Field(default=None, max_length=40)
    amount_cents: int = Field(default=0, ge=0, le=MAX_TAX_CENTS)
    tax_amount_cents: int = Field(default=0, ge=0, le=MAX_TAX_CENTS)
    issuance_cost_cents: int = Field(default=0, ge=0, le=MAX_ISSUANCE_COST_CENTS)
    provider: Optional[str] = Field(default=None, max_length=80)
    document_url: Optional[str] = Field(default=None, max_length=1000)
    xml_ref: Optional[str] = Field(default=None, max_length=1000)
    rejection_reason: Optional[str] = Field(default=None, max_length=500)
    request_id: Optional[str] = Field(default=None, max_length=64)
    case_id: Optional[str] = Field(default=None, max_length=64)
    client_id: Optional[str] = Field(default=None, max_length=64)
    external_id: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("doc_type")
    @classmethod
    def _doc_type_ok(cls, v: str) -> str:
        if v not in _DOC_TYPES:
            raise ValueError("tipo de nota inválido")
        return v

    @field_validator("status")
    @classmethod
    def _doc_status_ok(cls, v: str) -> str:
        if v not in _DOC_STATUS:
            raise ValueError("status de nota inválido")
        return v

    @field_validator("document_url", "xml_ref")
    @classmethod
    def _pointer_ok(cls, v):
        return _safe_pointer(v)

    @model_validator(mode="after")
    def _consistency(self):
        # Não marca nota "autorizada" sem número real (não forja numeração fiscal, 12.5).
        if self.status == "authorized" and not (self.numero and self.numero.strip()):
            raise ValueError("número é obrigatório para nota emitida/autorizada")
        if self.status == "rejected" and not (self.rejection_reason and self.rejection_reason.strip()):
            raise ValueError("motivo é obrigatório para nota rejeitada")
        return self
