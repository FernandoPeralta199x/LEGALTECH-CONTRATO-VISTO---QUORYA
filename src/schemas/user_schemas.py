"""Schemas de usuários (Pydantic 2). Alinhados ao schema real de `public.users`:
role ∈ {admin, analyst, viewer}; status ∈ {active, inactive}.

Signup público NÃO aceita `role` (criado sempre como `viewer` — menor privilégio);
a promoção de papel é feita por admin via `update_user`.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

ROLE_PATTERN = "^(admin|analyst|viewer)$"
STATUS_PATTERN = "^(active|inactive)$"


def _check_password_strength(v: str) -> str:
    # bcrypt opera sobre no máx. 72 bytes (e rejeita acima disso) → validar em bytes
    # para devolver 400 em vez de estourar no hash (500).
    if len(v.encode("utf-8")) > 72:
        raise ValueError("não pode exceder 72 bytes")
    if not any(c.isupper() for c in v):
        raise ValueError("deve conter pelo menos uma letra maiúscula")
    if not any(c.isdigit() for c in v):
        raise ValueError("deve conter pelo menos um número")
    return v


class UserLoginSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class UserSignupSchema(BaseModel):
    """Cadastro público — sem `role` (o handler força `viewer`)."""
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
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
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _check_password_strength(v)
