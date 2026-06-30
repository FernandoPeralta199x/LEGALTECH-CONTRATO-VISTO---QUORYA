"""Handlers Lambda de `cases` (migração FastAPI→Serverless, Fase 2).

Protegidos pelo JWT Authorizer (via @require_user) e isolados por RLS via
tenant_tx (app.user_id/app.user_role). Alinhados ao schema real: `cases` NÃO tem
`updated_at`; `created_by` é gravado para a policy de RLS. Erros internos não
vazam ao cliente.
"""
import json
import logging

from psycopg2.extras import Json
from pydantic import ValidationError

from src.schemas.case_schemas import CaseCreate, CaseUpdate
from src.services.database import tenant_tx
from src.utils.context import require_user, require_writer
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import fmt_validation_error as _fmt, parse_json_body as _parse_body, parse_pagination as _paginate, valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


class _ClientNotFound(Exception):
    """O client_id informado não existe."""


@require_user
@require_writer
def create_case(event, context):
    user = event["user"]
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = CaseCreate(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    client_id = _valid_uuid(data.client_id)
    if not client_id:
        return error_response(400, "client_id inválido")
    metadata = {"description": data.description} if data.description else None

    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            # Valida a existência do cliente para evitar erro de FK (→ 500 opaco).
            cur.execute("SELECT 1 FROM public.clients WHERE id = %s", (client_id,))
            if cur.fetchone() is None:
                raise _ClientNotFound()
            cur.execute(
                "INSERT INTO public.cases"
                " (client_id, case_type, priority, created_by, metadata)"
                " VALUES (%s, %s, %s, %s, %s)"
                " RETURNING id, status, created_at",
                (client_id, data.case_type, data.priority, user["user_id"],
                 Json(metadata) if metadata else None),
            )
            row = cur.fetchone()
    except _ClientNotFound:
        return error_response(400, "client_id inexistente")
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_CREATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao criar caso")

    logger.info(json.dumps({
        "event": "CASE_CREATED", "case_id": str(row["id"]),
        "created_by": user["user_id"],
    }))
    return success_response(201, "Caso criado com sucesso", {
        "id": str(row["id"]), "status": row["status"],
        "created_at": str(row["created_at"]),
    })


@require_user
def get_case(event, context):
    user = event["user"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            cur.execute(
                "SELECT id, client_id, case_type, status, priority, created_by,"
                " assigned_to, created_at, completed_at FROM public.cases"
                " WHERE id = %s",
                (case_id,),
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_GET_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao obter caso")
    if not row:
        return error_response(404, "Caso não encontrado")
    return success_response(200, "Caso encontrado", _serialize(row))


@require_user
def list_cases(event, context):
    user = event["user"]
    params = event.get("queryStringParameters") or {}
    pag, perr = _paginate(params)
    if perr:
        return perr
    page, page_size, offset = pag
    try:
        # A RLS já filtra os casos visíveis ao usuário (não filtramos por mão).
        with tenant_tx(user["user_id"], user["role"]) as cur:
            cur.execute(
                "SELECT id, client_id, case_type, status, priority, created_by,"
                " assigned_to, created_at, completed_at FROM public.cases"
                " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (page_size, offset),
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_LIST_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao listar casos")
    return success_response(
        200, f"{len(rows)} casos encontrados", [_serialize(r) for r in rows]
    )


@require_user
@require_writer
def update_case(event, context):
    user = event["user"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = CaseUpdate(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    fields, values = [], []
    if data.status is not None:
        fields.append("status = %s")
        values.append(data.status)
        # completed_at coerente com o status (literal SQL; status é validado por pattern)
        fields.append("completed_at = " + ("NOW()" if data.status == "completed" else "NULL"))
    if data.priority is not None:
        fields.append("priority = %s")
        values.append(data.priority)
    if data.assigned_to is not None:
        assigned = _valid_uuid(data.assigned_to)
        if not assigned:
            return error_response(400, "assigned_to inválido")
        fields.append("assigned_to = %s")
        values.append(assigned)
    if not fields:
        return error_response(400, "Nenhum campo para atualizar")
    values.append(case_id)

    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            cur.execute(
                f"UPDATE public.cases SET {', '.join(fields)} WHERE id = %s",
                tuple(values),
            )
            updated = cur.rowcount
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_UPDATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao atualizar caso")
    if not updated:
        return error_response(404, "Caso não encontrado")
    logger.info(json.dumps({"event": "CASE_UPDATED", "case_id": case_id}))
    return success_response(200, "Caso atualizado com sucesso")


@require_user
@require_writer
def delete_case(event, context):
    user = event["user"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            cur.execute("DELETE FROM public.cases WHERE id = %s", (case_id,))
            deleted = cur.rowcount
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_DELETE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao deletar caso")
    if not deleted:
        return error_response(404, "Caso não encontrado")
    logger.info(json.dumps({"event": "CASE_DELETED", "case_id": case_id}))
    return success_response(200, "Caso deletado com sucesso")


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "client_id": str(row["client_id"]),
        "case_type": row["case_type"],
        "status": row["status"],
        "priority": row["priority"],
        "created_by": str(row["created_by"]) if row["created_by"] else None,
        "assigned_to": str(row["assigned_to"]) if row["assigned_to"] else None,
        "created_at": str(row["created_at"]) if row["created_at"] else None,
        "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
    }
