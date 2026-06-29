"""Handlers Lambda de `case_results` (migração FastAPI→Serverless, Fase 2).

Protegidos pelo JWT Authorizer (@require_user) e isolados por RLS via tenant_tx.
Criação só é permitida para um `case` VISÍVEL ao usuário (verificado na mesma
transação) — fecha o furo apontado na revisão (resultado em caso alheio).
"""
import json
import logging

from psycopg2.extras import Json
from pydantic import ValidationError

from src.schemas.case_schemas import CaseResultCreate, CaseResultUpdate
from src.services.database import tenant_tx
from src.utils.context import require_user, require_writer
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import fmt_validation_error as _fmt, parse_json_body as _parse_body, valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


class _CaseNotVisible(Exception):
    """O case referenciado não existe ou não é visível ao usuário (RLS)."""


@require_user
@require_writer
def create_case_result(event, context):
    user = event["user"]
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = CaseResultCreate(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    case_id = _valid_uuid(data.case_id)
    if not case_id:
        return error_response(400, "case_id inválido")

    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            # Atômico (sem TOCTOU): só insere se o case for VISÍVEL ao usuário —
            # a RLS de `cases` filtra o EXISTS; 0 linhas inseridas => 404.
            cur.execute(
                "INSERT INTO public.case_results"
                " (case_id, result_type, result_title, result_data, risk_level,"
                "  confidence_score, summary_text, detailed_findings,"
                "  recommendations, created_by)"
                " SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
                " WHERE EXISTS (SELECT 1 FROM public.cases WHERE id = %s)"
                " RETURNING id, created_at",
                (case_id, data.result_type, data.result_title, Json(data.findings),
                 data.risk_level, data.confidence_score, data.summary_text,
                 data.detailed_findings, data.recommendations, user["user_id"],
                 case_id),
            )
            row = cur.fetchone()
            if row is None:
                raise _CaseNotVisible()
    except _CaseNotVisible:
        return error_response(404, "Caso não encontrado ou sem acesso")
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_RESULT_CREATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao criar resultado")

    logger.info(json.dumps({
        "event": "CASE_RESULT_CREATED", "result_id": str(row["id"]),
        "case_id": case_id, "risk_level": data.risk_level,
    }))
    return success_response(201, "Resultado criado com sucesso", {
        "id": str(row["id"]), "case_id": case_id,
        "created_at": str(row["created_at"]),
    })


@require_user
def get_case_result(event, context):
    user = event["user"]
    result_id = _valid_uuid((event.get("pathParameters") or {}).get("resultId"))
    if not result_id:
        return error_response(400, "resultId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            cur.execute(
                "SELECT id, case_id, result_type, result_title, result_data,"
                " risk_level, confidence_score, summary_text, created_at"
                " FROM public.case_results WHERE id = %s",
                (result_id,),
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_RESULT_GET_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao obter resultado")
    if not row:
        return error_response(404, "Resultado não encontrado")
    return success_response(200, "Resultado encontrado", _serialize(row))


@require_user
def list_case_results(event, context):
    user = event["user"]
    params = event.get("queryStringParameters") or {}
    case_id = _valid_uuid(params.get("caseId"))
    if not case_id:
        return error_response(400, "Parâmetro caseId inválido")
    try:
        page = max(int(params.get("page", 1)), 1)
        page_size = min(max(int(params.get("page_size", 50)), 1), 100)
    except (ValueError, TypeError):
        return error_response(400, "page/page_size inválidos")
    offset = (page - 1) * page_size
    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            cur.execute(
                "SELECT id, case_id, result_type, result_title, risk_level,"
                " confidence_score, created_at FROM public.case_results"
                " WHERE case_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (case_id, page_size, offset),
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_RESULT_LIST_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao listar resultados")
    return success_response(
        200, f"{len(rows)} resultados encontrados", [_serialize(r) for r in rows]
    )


@require_user
@require_writer
def update_case_result(event, context):
    user = event["user"]
    result_id = _valid_uuid((event.get("pathParameters") or {}).get("resultId"))
    if not result_id:
        return error_response(400, "resultId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = CaseResultUpdate(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    fields, values = [], []
    if data.risk_level is not None:
        fields.append("risk_level = %s")
        values.append(data.risk_level)
    if data.summary_text is not None:
        fields.append("summary_text = %s")
        values.append(data.summary_text)
    if data.recommendations is not None:
        fields.append("recommendations = %s")
        values.append(data.recommendations)
    if not fields:
        return error_response(400, "Nenhum campo para atualizar")
    values.append(result_id)

    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            cur.execute(
                f"UPDATE public.case_results SET {', '.join(fields)} WHERE id = %s",
                tuple(values),
            )
            updated = cur.rowcount
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_RESULT_UPDATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao atualizar resultado")
    if not updated:
        return error_response(404, "Resultado não encontrado")
    return success_response(200, "Resultado atualizado com sucesso")


@require_user
@require_writer
def delete_case_result(event, context):
    user = event["user"]
    result_id = _valid_uuid((event.get("pathParameters") or {}).get("resultId"))
    if not result_id:
        return error_response(400, "resultId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            cur.execute(
                "DELETE FROM public.case_results WHERE id = %s", (result_id,)
            )
            deleted = cur.rowcount
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_RESULT_DELETE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao deletar resultado")
    if not deleted:
        return error_response(404, "Resultado não encontrado")
    return success_response(200, "Resultado deletado com sucesso")


def _serialize(row) -> dict:
    data = {
        "id": str(row["id"]),
        "case_id": str(row["case_id"]),
        "result_type": row["result_type"],
        "result_title": row.get("result_title"),
        "risk_level": row["risk_level"],
        "confidence_score": (
            float(row["confidence_score"])
            if row.get("confidence_score") is not None else None
        ),
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
    }
    if "result_data" in row:
        data["findings"] = row["result_data"]
    if "summary_text" in row:
        data["summary_text"] = row["summary_text"]
    return data
