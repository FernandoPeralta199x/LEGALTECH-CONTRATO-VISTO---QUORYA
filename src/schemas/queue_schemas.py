"""Contratos da fila assíncrona (SP1) — espelham a referência (modules/queues/schemas).

DocumentProcessingJob é a mensagem enfileirada (SQS na AWS, inline em dev). A metadata
nunca pode carregar conteúdo do documento (LGPD/segurança) — só ponteiros. WorkerResult
é o resultado do processamento de um job; EnqueueDocumentProcessingResult é a resposta
do endpoint de enfileiramento (contrato do frontend).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

# chaves que NUNCA podem aparecer na metadata do job (conteúdo do documento)
SENSITIVE_METADATA_KEYS = frozenset({
    "body", "content", "document_content", "document_text",
    "file_content", "raw_content", "raw_text", "text",
})


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in SENSITIVE_METADATA_KEYS:
                return True
            if _contains_sensitive_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


class DocumentProcessingJob(BaseModel):
    """Mensagem de processamento de documento (SP2) enfileirada para o worker."""
    model_config = ConfigDict(extra="forbid")

    job_id: UUID = Field(default_factory=uuid4)
    job_type: Literal["document_processing"] = "document_processing"
    agent_type: Literal["document_processing_local"] = "document_processing_local"
    organization_id: UUID
    case_id: UUID
    document_id: UUID
    attempt: int = Field(default=1, ge=1)
    requested_by: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("metadata")
    @classmethod
    def _reject_sensitive_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _contains_sensitive_key(value):
            raise ValueError("Metadata do job não pode conter conteúdo do documento.")
        return value


class EnqueueDocumentProcessingResult(BaseModel):
    """Resposta do endpoint de enfileiramento (contrato EnqueueProcessingResult do front)."""
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["queued"] = "queued"
    queue_backend: Literal["inline", "sqs"]
    document_id: UUID


class WorkerResult(BaseModel):
    """Resultado do processamento de um job pelo worker."""
    model_config = ConfigDict(extra="forbid")

    job_id: UUID | None = None
    document_id: UUID | None = None
    status: Literal["empty", "completed", "skipped", "failed", "retrying"]
    reason: str | None = None
