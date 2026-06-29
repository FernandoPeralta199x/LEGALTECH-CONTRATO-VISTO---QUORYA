"""Contexto do usuário autenticado a partir do JWT Authorizer (compartilhado).

O API Gateway injeta o payload validado em
``event.requestContext.authorizer.context = {user_id, email, role}``.
Os handlers NÃO revalidam o token; apenas leem este contexto.
"""
import logging
import uuid

from src.utils.helpers import error_response

logger = logging.getLogger()

VALID_ROLES = {"admin", "analyst", "viewer"}


def get_user_from_event(event):
    """Extrai e valida {user_id (UUID), email, role} do contexto do authorizer.

    Retorna o dict do usuário ou ``None`` se ausente/inválido.
    """
    try:
        ctx = event["requestContext"]["authorizer"]["context"]
        user_id = str(uuid.UUID(str(ctx["user_id"])))  # canoniza; exige UUID válido
        role = ctx["role"]
        if role not in VALID_ROLES:
            return None
        return {"user_id": user_id, "email": ctx.get("email", ""), "role": role}
    except (KeyError, ValueError, TypeError):
        return None


def require_user(handler_func):
    """Decorator: exige usuário autenticado válido; injeta ``event['user']``."""

    def wrapper(event, context):
        user = get_user_from_event(event)
        if not user:
            return error_response(401, "Usuário não autenticado")
        event["user"] = user
        return handler_func(event, context)

    return wrapper
