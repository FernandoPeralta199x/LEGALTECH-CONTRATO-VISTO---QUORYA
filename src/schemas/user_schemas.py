"""Schemas de usuários (Pydantic 2). Alinhados ao schema real de `public.users`:
role ∈ {admin, analyst, viewer}; status ∈ {active, inactive}.

Signup público NÃO aceita `role` (extra=forbid): o handler cria o primeiro usuário da
nova organização como `admin` (dono do tenant). Promoção/alteração de papel é por admin
via `update_user`. (Rate limit / verificação de e-mail no signup ficam para a Fase 7.)

Política de senha (S-03): alinhada ao frontend (getPasswordRequirements) — mínimo 12
caracteres, com letra maiúscula, minúscula e caractere especial.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

ROLE_PATTERN = "^(admin|analyst|viewer)$"
STATUS_PATTERN = "^(active|inactive)$"
PASSWORD_MIN_LENGTH = 12


def _check_password_strength(v: str) -> str:
    # bcrypt opera sobre no máx. 72 bytes (e rejeita acima disso) → validar em bytes
    # para devolver 400 em vez de estourar no hash (500).
    if len(v.encode("utf-8")) > 72:
        raise ValueError("não pode exceder 72 bytes")
    if len(v) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"deve ter pelo menos {PASSWORD_MIN_LENGTH} caracteres")
    if not any(c.isupper() for c in v):
        raise ValueError("deve conter pelo menos uma letra maiúscula")
    if not any(c.islower() for c in v):
        raise ValueError("deve conter pelo menos uma letra minúscula")
    if not any(not c.isalnum() for c in v):
        raise ValueError("deve conter pelo menos um caractere especial")
    return v


class UserLoginSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class UserSignupSchema(BaseModel):
    """Cadastro público — sem `role` (o handler cria o 1º usuário da org como `admin`)."""
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    # max 72: bcrypt opera sobre <=72 bytes; o cap real de força é em _check_password_strength.
    # Antes estava 18 (bloqueava passphrases; inconsistente com login=128 e o cap de bcrypt) — be-sec-04.
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=72)
    name: str = Field(..., min_length=2, max_length=255)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _check_password_strength(v)


class UserUpdateSchema(BaseModel):
    """Atualização de usuário. `role`/`status` só são aplicados por admin
    (validado no handler)."""
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=2, max_length=255)
    role: Optional[str] = Field(default=None, pattern=ROLE_PATTERN)
    status: Optional[str] = Field(default=None, pattern=STATUS_PATTERN)


class ForgotPasswordSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class ResetPasswordSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=20)
    # alinhado à política de signup (min 12, max 72 bytes via _check_password_strength) — be-sec-04
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=72)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _check_password_strength(v)
