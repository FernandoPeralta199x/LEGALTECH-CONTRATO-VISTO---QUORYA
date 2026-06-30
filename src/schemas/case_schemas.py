"""Schemas de cases/case_results (Pydantic 2). Alinhados ao schema real do banco
`contrato_visto`. Usa `pattern=` (não `regex=`, removido no Pydantic 2)."""
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

CASE_TYPE_PATTERN = "^(due_diligence_party|due_diligence_asset|contract_analysis)$"
CASE_STATUS_PATTERN = "^(open|in_progress|completed|closed)$"
PRIORITY_PATTERN = "^(low|normal|high|urgent)$"
RISK_LEVEL_PATTERN = "^(low|medium|high|critical)$"


class CaseCreate(BaseModel):
    # tolerante ao payload do "Criar caso rápido" do front (title/product/notes vêm
    # dentro de metadata); extras desconhecidos são ignorados, não rejeitados.
    model_config = ConfigDict(extra="ignore")

    client_id: str
    case_type: str = Field(..., pattern=CASE_TYPE_PATTERN)
    priority: str = Field(default="normal", pattern=PRIORITY_PATTERN)
    description: Optional[str] = None  # gravado em cases.metadata (jsonb)
    title: Optional[str] = Field(default=None, max_length=255)
    product_type: Optional[str] = Field(default=None, max_length=40)
    metadata: Optional[dict] = None  # caso rápido: {title, product, notes, source}


class CaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[str] = Field(default=None, pattern=CASE_STATUS_PATTERN)
    priority: Optional[str] = Field(default=None, pattern=PRIORITY_PATTERN)
    assigned_to: Optional[str] = None


class CaseResultCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    result_type: str = Field(..., max_length=50)
    result_title: Optional[str] = Field(default=None, max_length=255)
    findings: dict[str, Any] = Field(default_factory=dict)  # -> result_data (jsonb)
    risk_level: str = Field(..., pattern=RISK_LEVEL_PATTERN)
    confidence_score: Optional[float] = Field(default=None, ge=0, le=1)
    summary_text: Optional[str] = None
    detailed_findings: Optional[str] = None
    recommendations: Optional[str] = None


class CaseResultUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: Optional[str] = Field(default=None, pattern=RISK_LEVEL_PATTERN)
    summary_text: Optional[str] = None
    recommendations: Optional[str] = None
