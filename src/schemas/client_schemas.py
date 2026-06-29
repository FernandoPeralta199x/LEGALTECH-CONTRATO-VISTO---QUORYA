"""Schemas de clients (Pydantic 2). Alinhados ao schema real de `public.clients`:
`legal_name`, `document_type` ∈ {cpf, cnpj}, `document_number` (só dígitos: 11=CPF,
14=CNPJ), email/phone/endereço opcionais, `status` ∈ {active, inactive}.

`clients` é um catálogo COMPARTILHADO (sem `created_by`, sem RLS).
"""
import re
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

DOC_TYPE_PATTERN = "^(cpf|cnpj)$"
STATUS_PATTERN = "^(active|inactive)$"
STATE_PATTERN = "^[A-Za-z]{2}$"


class ClientCreateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(..., min_length=2, max_length=255)
    document_type: str = Field(..., pattern=DOC_TYPE_PATTERN)
    document_number: str = Field(..., max_length=20)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    address_street: Optional[str] = Field(default=None, max_length=255)
    address_city: Optional[str] = Field(default=None, max_length=100)
    address_state: Optional[str] = Field(default=None, pattern=STATE_PATTERN)
    address_zip: Optional[str] = Field(default=None, max_length=10)

    @field_validator("document_number")
    @classmethod
    def clean_document_number(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if len(digits) not in (11, 14):
            raise ValueError("deve ter 11 (CPF) ou 14 (CNPJ) dígitos")
        return digits

    @model_validator(mode="after")
    def check_document_coherence(self):
        expected = 11 if self.document_type == "cpf" else 14
        if len(self.document_number) != expected:
            raise ValueError(
                f"document_number incompatível com document_type={self.document_type}"
            )
        return self


class ClientUpdateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: Optional[str] = Field(default=None, min_length=2, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    address_street: Optional[str] = Field(default=None, max_length=255)
    address_city: Optional[str] = Field(default=None, max_length=100)
    address_state: Optional[str] = Field(default=None, pattern=STATE_PATTERN)
    address_zip: Optional[str] = Field(default=None, max_length=10)
    status: Optional[str] = Field(default=None, pattern=STATUS_PATTERN)
