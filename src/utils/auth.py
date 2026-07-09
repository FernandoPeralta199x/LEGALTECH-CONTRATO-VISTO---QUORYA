"""Emissão de JWT (HS256) para o login.

Os handlers protegidos NÃO revalidam o token: o JWT Authorizer valida no API
Gateway e injeta o contexto (ver `src/utils/context.py`).
"""
import os
from datetime import datetime, timedelta, timezone

import jwt

# Default 2h (era 8h): mitigação da defasagem de revogação — como os handlers
# ainda confiam nos claims do token (papel/status), o TTL menor encurta a janela em
# que um usuário rebaixado/desativado mantém privilégio. Fix definitivo = re-checar
# papel/status no banco nas rotas de escrita/admin (follow-up be-auth-01/be-sec-02).
_TOKEN_TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "2"))
TOKEN_TTL_SECONDS = _TOKEN_TTL_HOURS * 3600  # exposto no login (expires_in)


def create_access_token(payload: dict) -> str:
    """Emite um JWT HS256 com expiração. SEM default de segredo (fail-closed):
    se ``JWT_SECRET_KEY`` não estiver configurado, levanta ``KeyError``."""
    secret_key = os.environ["JWT_SECRET_KEY"]
    now = datetime.now(timezone.utc)
    claims = {**payload, "iat": now, "exp": now + timedelta(hours=_TOKEN_TTL_HOURS)}
    return jwt.encode(claims, secret_key, algorithm="HS256")
