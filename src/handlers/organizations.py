"""Handlers Lambda de `organizations` — "operar como org" (Fase 2).

A firma (perfil `administrador`, org `operador`) LISTA as orgs-cliente e PROCESSA os
casos de uma delas de forma CROSS-tenant, sempre AUDITADA e nunca por bypass da RLS:
- ``list_client_orgs``  -> GET /organizations/clients (leitura cross-org controlada
  via a função SECURITY DEFINER ``public.list_client_organizations``, migration 029).
- ``list_org_cases``    -> GET /organizations/{orgId}/cases (impersonação: a RLS é
  escopada ao alvo por ``operator_tx``, que valida o operador + o alvo e grava
  ``OPERATOR_IMPERSONATION`` na trilha, tudo antes de qualquer leitura).

A AUTORIDADE de operar cross-tenant vem do BANCO (assert_operator_admin), além do
gate de token (@require_role/@require_perfil) — defesa em profundidade.
"""
import json
import logging

import psycopg2

from src.services.database import operator_tx, tenant_tx
# Reuso da forma canônica de caso (mesma projeção/serialização da lista normal, para
# o frontend consumir a visão "operar como org" sem um mapeador novo).
from src.handlers.cases import _CASE_COLS, _serialize as _serialize_case
from src.utils.context import (LIBERATABLE_TELAS, effective_telas, require_perfil,
                               require_role, require_user)
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import (parse_json_body as _parse_body,
                                 parse_pagination as _paginate, valid_uuid as _valid_uuid)
from src.utils.pii import mask_document
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


def _serialize_org(row) -> dict:
    """Org-cliente para a firma escolher — documento MASCARADO (minimização LGPD)."""
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "type": row["type"],
        "document_masked": mask_document(row["document"]),
        "document_type": row["document_type"],
        "status": row["status"],
        "created_at": str(row["created_at"]) if row["created_at"] else None,
    }


@require_user
@require_role("admin")
@require_perfil("administrador")
def list_client_orgs(event, context):
    """Lista as organizações-cliente (empresarial/individual) para a firma operar."""
    user = event["user"]
    try:
        # Contexto do próprio operador (seta app.user_id p/ a função autorizar).
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute("SELECT id, name, type, document, document_type, status,"
                        " created_at FROM public.list_client_organizations()")
            rows = cur.fetchall()
    except psycopg2.errors.InsufficientPrivilege:
        # A função reconsulta o banco: operador rebaixado/inativo desde o token.
        return error_response(403, "Acesso negado")
    except Exception as e:
        logger.error(json.dumps({"event": "CLIENT_ORGS_LIST_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}), exc_info=True)
        return error_response(500, "Erro ao listar organizações de clientes")
    return success_response(200, f"{len(rows)} organizações de clientes", {
        "items": [_serialize_org(r) for r in rows],
        "total": len(rows),
    })


@require_user
@require_role("admin")
@require_perfil("administrador")
def list_org_cases(event, context):
    """Casos de uma org-cliente (impersonação auditada). Mesma forma da lista normal."""
    user = event["user"]
    org_id = _valid_uuid((event.get("pathParameters") or {}).get("orgId"))
    if not org_id:
        return error_response(400, "orgId inválido")

    params = event.get("queryStringParameters") or {}
    pag, perr = _paginate(params)
    if perr:
        return perr
    page, page_size, offset = pag

    endpoint = f"GET /organizations/{org_id}/cases"
    try:
        # operator_tx: valida operador + alvo e AUDITA antes de ler (RLS -> alvo).
        with operator_tx(user["user_id"], user["role"], org_id, endpoint) as cur:
            cur.execute("SELECT count(*) AS n FROM public.cases WHERE deleted_at IS NULL")
            total = cur.fetchone()["n"]
            cur.execute(
                f"SELECT {_CASE_COLS},"
                " (SELECT count(*) FROM public.case_parties cp"
                "  WHERE cp.case_id = public.cases.id AND cp.deleted_at IS NULL)"
                " AS parties_count,"
                " (SELECT cl.legal_name FROM public.clients cl"
                "  WHERE cl.id = public.cases.client_id)"
                " AS client_name"
                " FROM public.cases WHERE deleted_at IS NULL"
                " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (page_size, offset),
            )
            rows = cur.fetchall()
    except psycopg2.errors.InsufficientPrivilege:      # 42501: não é operador
        return error_response(403, "Acesso negado")
    except psycopg2.errors.InvalidParameterValue:      # 22023: alvo não é org-cliente
        return error_response(404, "Organização de cliente não encontrada")
    except Exception as e:
        logger.error(json.dumps({"event": "ORG_CASES_LIST_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}), exc_info=True)
        return error_response(500, "Erro ao listar casos da organização")

    total_pages = (total + page_size - 1) // page_size if page_size else 1
    logger.info(json.dumps({"event": "OPERATOR_VIEWED_ORG_CASES",
                            "operator": user["user_id"], "target_org": org_id,
                            "count": len(rows)}))
    return success_response(200, f"{total} casos encontrados", {
        "items": [_serialize_case(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    })


def _serialize_org_user(row) -> dict:
    """Usuário de uma org-cliente + telas (Modelo B — para o admin liberar abas)."""
    perfil = row["perfil"]
    telas_extra = list(row["telas_extra"] or [])
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "status": row["status"],
        "perfil": perfil,
        "telas_extra": telas_extra,
        "telas": sorted(effective_telas(perfil, telas_extra)),
        "created_at": str(row["created_at"]) if row["created_at"] else None,
    }


@require_user
@require_role("admin")
@require_perfil("administrador")
def list_org_users(event, context):
    """Usuários de uma org-cliente + telas efetivas (impersonação auditada).
    Base da tela Administração → Configuração de Perfil (Modelo B)."""
    user = event["user"]
    org_id = _valid_uuid((event.get("pathParameters") or {}).get("orgId"))
    if not org_id:
        return error_response(400, "orgId inválido")
    endpoint = f"GET /organizations/{org_id}/users"
    try:
        with operator_tx(user["user_id"], user["role"], org_id, endpoint) as cur:
            cur.execute(
                "SELECT id, email, name, role, status, perfil, telas_extra, created_at"
                " FROM public.users WHERE status = 'active' ORDER BY created_at")
            rows = cur.fetchall()
    except psycopg2.errors.InsufficientPrivilege:      # 42501: não é operador
        return error_response(403, "Acesso negado")
    except psycopg2.errors.InvalidParameterValue:      # 22023: alvo não é org-cliente
        return error_response(404, "Organização de cliente não encontrada")
    except Exception as e:
        logger.error(json.dumps({"event": "ORG_USERS_LIST_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}), exc_info=True)
        return error_response(500, "Erro ao listar usuários da organização")
    logger.info(json.dumps({"event": "OPERATOR_VIEWED_ORG_USERS",
                            "operator": user["user_id"], "target_org": org_id,
                            "count": len(rows)}))
    return success_response(200, f"{len(rows)} usuários", {
        "items": [_serialize_org_user(r) for r in rows],
        "total": len(rows),
    })


@require_user
@require_role("admin")
@require_perfil("administrador")
def update_org_user_telas(event, context):
    """Libera/revoga abas (telas_extra) de um usuário de uma org-cliente — Modelo B.
    Cross-tenant AUDITADO via operator_tx; só telas LIBERÁVEIS; nunca em administrador."""
    user = event["user"]
    path = event.get("pathParameters") or {}
    org_id = _valid_uuid(path.get("orgId"))
    target = _valid_uuid(path.get("userId"))
    if not org_id or not target:
        return error_response(400, "orgId/userId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    telas = body.get("telas")
    if not isinstance(telas, list) or not all(isinstance(t, str) for t in telas):
        return error_response(400, "'telas' deve ser uma lista de strings")
    invalid = sorted(set(telas) - LIBERATABLE_TELAS)
    if invalid:
        return error_response(400, f"telas não liberáveis: {', '.join(invalid)}")
    normalized = sorted(set(telas))
    endpoint = f"PATCH /organizations/{org_id}/users/{target}/telas"
    try:
        with operator_tx(user["user_id"], user["role"], org_id, endpoint) as cur:
            # A RLS já escopa ao alvo; não mexemos em administradores (defesa).
            cur.execute(
                "UPDATE public.users SET telas_extra = %s, updated_at = NOW()"
                " WHERE id = %s AND perfil <> 'administrador'",
                (normalized, target))
            updated = cur.rowcount
    except psycopg2.errors.InsufficientPrivilege:
        return error_response(403, "Acesso negado")
    except psycopg2.errors.InvalidParameterValue:
        return error_response(404, "Organização de cliente não encontrada")
    except Exception as e:
        logger.error(json.dumps({"event": "ORG_USER_TELAS_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}), exc_info=True)
        return error_response(500, "Erro ao atualizar telas do usuário")
    if not updated:
        return error_response(404, "Usuário não encontrado")
    logger.info(json.dumps({"event": "OPERATOR_SET_USER_TELAS",
                            "operator": user["user_id"], "target_org": org_id,
                            "target_user": target, "telas": normalized}))
    return success_response(200, "Telas atualizadas", {"telas_extra": normalized})
