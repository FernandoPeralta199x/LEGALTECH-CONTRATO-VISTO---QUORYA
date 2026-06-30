"""Contexto do usuário autenticado a partir do JWT Authorizer (compartilhado).

O JWT Authorizer (TOKEN, REST API) devolve ``context`` que o API Gateway entrega
ao handler ACHATADO em ``event.requestContext.authorizer.<key>`` (valores como
string). Aceitamos também o aninhado ``...authorizer.context`` (HTTP API / testes).
Os handlers NÃO revalidam o token; apenas leem este contexto.
"""
import uuid

from src.utils.helpers import error_response

VALID_ROLES = {"admin", "analyst", "viewer"}
# Papéis autorizados a escrever (create/update/delete). `viewer` é somente leitura.
WRITE_ROLES = {"admin", "analyst"}


def _authorizer_claims(event):
    """Lê os claims do authorizer cobrindo os dois shapes do API Gateway.

    - REST API (TOKEN authorizer): achatado em ``authorizer.<key>``.
    - HTTP API / testes: aninhado em ``authorizer.context``.
    """
    auth = (event.get("requestContext") or {}).get("authorizer") or {}
    nested = auth.get("context")
    return nested if isinstance(nested, dict) else auth


def get_user_from_event(event):
    """Extrai e valida {user_id (UUID), email, role} do contexto do authorizer.

    Retorna o dict do usuário ou ``None`` se ausente/inválido.
    """
    try:
        ctx = _authorizer_claims(event)
        user_id = str(uuid.UUID(str(ctx["user_id"])))  # canoniza; exige UUID válido
        organization_id = str(uuid.UUID(str(ctx["organization_id"])))  # exige org
        role = ctx["role"]
        if role not in VALID_ROLES:
            return None
        return {"user_id": user_id, "organization_id": organization_id,
                "email": ctx.get("email", ""), "role": role}
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


def require_writer(handler_func):
    """Decorator: exige papel com permissão de escrita (admin/analyst).

    Deve ser aplicado ABAIXO de ``@require_user`` (que popula ``event['user']``).
    Bloqueia ``viewer`` (somente leitura) com 403.
    """

    def wrapper(event, context):
        user = event.get("user") or {}
        if user.get("role") not in WRITE_ROLES:
            return error_response(403, "Permissão insuficiente para esta operação")
        return handler_func(event, context)

    return wrapper


def require_role(*allowed_roles):
    """Decorator: exige que ``event['user']['role']`` esteja em ``allowed_roles``.

    Deve ser aplicado ABAIXO de ``@require_user``. Ex.: ``@require_role("admin")``.
    """

    def decorator(handler_func):
        def wrapper(event, context):
            user = event.get("user") or {}
            if user.get("role") not in allowed_roles:
                return error_response(403, "Acesso negado")
            return handler_func(event, context)

        return wrapper

    return decorator
