"""Utilitários de JWT (emissão e verificação). HS256.

Os handlers protegidos NÃO revalidam o token aqui: o JWT Authorizer valida no API
Gateway e injeta o contexto (ver `src/utils/context.py`). `verify_token` fica como
utilitário para validação fora do authorizer, se necessário.
"""
import os
from datetime import datetime, timedelta, timezone

import jwt

_TOKEN_TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "8"))


def create_access_token(payload: dict) -> str:
    """Emite um JWT HS256 com expiração. SEM default de segredo (fail-closed):
    se ``JWT_SECRET_KEY`` não estiver configurado, levanta ``KeyError``."""
    secret_key = os.environ["JWT_SECRET_KEY"]
    now = datetime.now(timezone.utc)
    claims = {**payload, "iat": now, "exp": now + timedelta(hours=_TOKEN_TTL_HOURS)}
    return jwt.encode(claims, secret_key, algorithm="HS256")


def verify_token(token):
    """Valida um JWT HS256. Fail-closed: sem segredo configurado, retorna ``None``."""
    secret_key = os.getenv("JWT_SECRET_KEY")
    if not secret_key:
        return None
    try:
        return jwt.decode(token, secret_key, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
