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

    ATENÇÃO: valida o papel do TOKEN (fast-path/defesa em profundidade). Rotas
    admin/escrita DEVEM reconsultar o estado atual no banco com
    ``load_active_caller``/``assert_active_admin`` dentro da transação (SEC-01..04/M2).
    """

    def decorator(handler_func):
        def wrapper(event, context):
            user = event.get("user") or {}
            if user.get("role") not in allowed_roles:
                return error_response(403, "Acesso negado")
            return handler_func(event, context)

        return wrapper

    return decorator


class CallerRevoked(Exception):
    """O caller foi rebaixado/desativado/removido no banco desde a emissão do token.

    O JWT Authorizer valida só assinatura+exp e injeta ``role``/``status``
    HISTÓRICOS; um usuário com um JWT antigo mantém o papel até o ``exp`` (~2h).
    As rotas admin/escrita fecham essa janela reconsultando o estado ATUAL no banco
    e levantando esta exceção — que o handler traduz para 403 (SEC-01..04/M2)."""


def load_active_caller(cur, user_id, organization_id) -> str:
    """Reconsulta ``role``/``status`` ATUAIS do caller no banco (não confia no token).

    Deve rodar na MESMA transação da operação, para fechar a janela de revogação.
    Levanta ``CallerRevoked`` se o usuário não existe na organização confiável do
    token ou não está ``active``. Retorna o papel atual (``admin``/``analyst``/``viewer``)."""
    cur.execute(
        "SELECT role, status FROM public.users WHERE id = %s AND organization_id = %s",
        (user_id, organization_id),
    )
    row = cur.fetchone()
    if not row:
        raise CallerRevoked("usuário não encontrado na organização")
    if row["status"] != "active":
        raise CallerRevoked("conta inativa")
    return row["role"]


def assert_active_admin(cur, user_id, organization_id) -> str:
    """Como ``load_active_caller``, mas exige papel ``admin`` ATUAL no banco."""
    role = load_active_caller(cur, user_id, organization_id)
    if role != "admin":
        raise CallerRevoked("papel administrativo revogado")
    return role


def assert_active_writer(cur, user_id, organization_id) -> str:
    """Como ``load_active_caller``, mas exige papel de ESCRITA (admin/analyst) ATUAL.

    Fecha a janela de revogação (~2h) do JWT nas rotas de escrita de negócio (SEC-01):
    ``@require_writer`` valida só o papel HISTÓRICO do token, então um usuário desativado
    ou rebaixado a ``viewer`` após o login manteria create/update/delete até o ``exp``.
    Chamada como 1ª instrução DENTRO do ``tenant_tx`` da operação, reconsulta o papel
    ATUAL no banco (mesma transação) e levanta ``CallerRevoked`` — que o handler traduz
    para 403. Retorna o papel atual (útil p/ decisões finas, ex.: máscara de PII)."""
    role = load_active_caller(cur, user_id, organization_id)
    if role not in WRITE_ROLES:
        raise CallerRevoked("permissão de escrita revogada")
    return role
