"""Schemas Pydantic do Pedido (wizard Novo Pedido) — POST /requests."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PartyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=50)
    person_type: str = "individual"
    document: str | None = Field(default=None, max_length=32)
    document_type: str | None = None
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    metadata: dict = Field(default_factory=dict)


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    mime_type: str | None = Field(default=None, max_length=120)
    size_bytes: int | None = Field(default=None, ge=0)
    storage_key: str | None = None


class RequestCreateSchema(BaseModel):
    """Payload do wizard: produto + partes + documento + módulos selecionados."""

    model_config = ConfigDict(extra="forbid")

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
