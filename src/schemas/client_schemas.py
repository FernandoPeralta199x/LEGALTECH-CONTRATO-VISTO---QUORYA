"""Schemas de clients (Pydantic 2). Alinhados ao schema real de `public.clients`:
`legal_name`, `document_type` ∈ {cpf, cnpj}, `document_number` (só dígitos: 11=CPF,
14=CNPJ), email/phone/endereço opcionais, `status` ∈ {active, inactive}.

`clients` é um catálogo COMPARTILHADO (sem `created_by`, sem RLS).
"""
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from src.schemas.br_documents import clean_document, is_valid_cnpj, is_valid_cpf

DOC_TYPE_PATTERN = "^(cpf|cnpj)$"
STATUS_PATTERN = "^(active|inactive)$"
STATE_PATTERN = "^[A-Za-z]{2}$"


def _map_client_aliases(data):
    """Mapeia o shape V2 do frontend para os campos do schema (name->legal_name,
    document/cpf/cnpj->document_number, address->address_street). Extras ignorados."""
    if not isinstance(data, dict):
        return data
    d = dict(data)
    if not d.get("legal_name"):
        d["legal_name"] = (d.get("name") or d.get("full_name")
                           or d.get("display_name") or d.get("company_name")
                           or d.get("trade_name"))
    if not d.get("document_number"):
        d["document_number"] = d.get("document") or d.get("cpf") or d.get("cnpj")
    if not d.get("document_type"):
        if d.get("cnpj"):
            d["document_type"] = "cnpj"
        elif d.get("cpf"):
            d["document_type"] = "cpf"
        elif d.get("document_number"):
            cleaned = clean_document(str(d.get("document_number") or ""))
            d["document_type"] = "cnpj" if len(cleaned) == 14 else "cpf"
    if d.get("address") and not d.get("address_street"):
        d["address_street"] = str(d["address"])[:255]
    return d


class ClientCreateSchema(BaseModel):
    # extra="ignore": aceita o shape V2 do frontend e ignora campos não mapeados
    model_config = ConfigDict(extra="ignore")

    legal_name: str = Field(..., min_length=2, max_length=255)
    document_type: str = Field(..., pattern=DOC_TYPE_PATTERN)
    document_number: str = Field(..., max_length=20)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    address_street: Optional[str] = Field(default=None, max_length=255)
    address_city: Optional[str] = Field(default=None, max_length=100)
    address_state: Optional[str] = Field(default=None, pattern=STATE_PATTERN)
    address_zip: Optional[str] = Field(default=None, max_length=10)
    rg: Optional[str] = Field(default=None, max_length=40)

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data):
        return _map_client_aliases(data)

    @field_validator("document_number")
    @classmethod
    def clean_document_number(cls, v: str) -> str:
        cleaned = clean_document(v)  # mantém alfanuméricos (CNPJ 2026)
        if len(cleaned) not in (11, 14):
            raise ValueError("deve ter 11 (CPF) ou 14 (CNPJ) caracteres")
        return cleaned

    @model_validator(mode="after")
    def check_document_coherence(self):
        expected = 11 if self.document_type == "cpf" else 14
        if len(self.document_number) != expected:
            raise ValueError(
                f"document_number incompatível com document_type={self.document_type}"
            )
        valido = is_valid_cpf if self.document_type == "cpf" else is_valid_cnpj
        if not valido(self.document_number):
            raise ValueError("document_number inválido (dígito verificador)")
        return self


class ClientUpdateSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    legal_name: Optional[str] = Field(default=None, min_length=2, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    address_street: Optional[str] = Field(default=None, max_length=255)
    address_city: Optional[str] = Field(default=None, max_length=100)
    address_state: Optional[str] = Field(default=None, pattern=STATE_PATTERN)
    address_zip: Optional[str] = Field(default=None, max_length=10)
    rg: Optional[str] = Field(default=None, max_length=40)
    status: Optional[str] = Field(default=None, pattern=STATUS_PATTERN)

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data):
        return _map_client_aliases(data)
