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


def _valid_cpf(cpf: str) -> bool:
    if len(cpf) != 11 or len(set(cpf)) == 1:
        return False
    for i in (9, 10):
        soma = sum(int(cpf[n]) * ((i + 1) - n) for n in range(i))
        dv = (soma * 10) % 11
        dv = 0 if dv == 10 else dv
        if dv != int(cpf[i]):
            return False
    return True


def _valid_cnpj(cnpj: str) -> bool:
    if len(cnpj) != 14 or len(set(cnpj)) == 1:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for pesos, i in ((pesos1, 12), (pesos2, 13)):
        soma = sum(int(cnpj[n]) * pesos[n] for n in range(i))
        dv = soma % 11
        dv = 0 if dv < 2 else 11 - dv
        if dv != int(cnpj[i]):
            return False
    return True


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
            digits = re.sub(r"\D", "", str(d.get("document_number") or ""))
            d["document_type"] = "cnpj" if len(digits) == 14 else "cpf"
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
        valido = _valid_cpf if self.document_type == "cpf" else _valid_cnpj
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
