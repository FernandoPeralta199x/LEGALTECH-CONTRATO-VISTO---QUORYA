"""Schemas Pydantic do Pedido (wizard Novo Pedido) — POST /requests."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# teto de tamanho declarado para upload (S-02): a chave do S3 é sempre gerada pelo
# backend (S-01), então o cliente nunca informa storage_key.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


class PartyInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=50)
    person_type: str = "individual"
    document: str | None = Field(default=None, max_length=32)
    document_type: str | None = None
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    metadata: dict = Field(default_factory=dict)


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    filename: str = Field(min_length=1, max_length=255)
    mime_type: str | None = Field(default=None, max_length=120)
    size_bytes: int | None = Field(default=None, ge=0, le=MAX_UPLOAD_BYTES)
    # storage_key NÃO é aceito do cliente (S-01: evita IDOR no bucket). O backend
    # sempre gera a chave canônica a partir de case_id/document_id.


class RequestCreateSchema(BaseModel):
    """Payload do wizard: produto + partes + documento + módulos selecionados."""

    model_config = ConfigDict(extra="ignore")

    product_type: str = Field(min_length=1, max_length=64)
    product_label: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    source_mode: str = "local"
    idempotency_key: str | None = Field(default=None, max_length=255)
    parties: list[PartyInput] = Field(default_factory=list)
    document: DocumentInput | None = None
    selected_modules: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
