"""Handlers de leitura do plano de triagem do caso (aba "Triagem").

GET /cases/{caseId}/triage — lista os módulos técnicos de triagem (gerados pelo
wizard), com status/provider/required. A EXECUÇÃO dos módulos é fase futura (SP3).
"""
import json
import logging

from src.services.database import tenant_tx
from src.services.triage_runner import run_case_triage
from src.utils.context import require_user, require_writer
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


def _serialize_module(row) -> dict:
    return {
        "id": str(row["id"]),
        "case_id": str(row["case_id"]),
        "module_key": row["module_key"],
        "module_label": row["module_label"],
        "provider": row["provider"],
        "status": row["status"],
        "source_mode": row["source_mode"],
        "required": row["required"],
        "reason": row["reason"],
        "attempts": row["attempts"],
        "summary": row["summary"],
        "started_at": str(row["started_at"]) if row["started_at"] else None,
        "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
    }


@require_user
def list_triage(event, context):
    """Lista os módulos de triagem do caso. 404 se o caso não é visível à org."""
    user = event["user"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute("SELECT 1 FROM public.cases WHERE id = %s", (case_id,))
            if cur.fetchone() is None:
                return error_response(404, "Caso não encontrado")
            cur.execute(
                "SELECT id, case_id, module_key, module_label, provider, status,"
                " source_mode, required, reason, attempts, summary, started_at,"
                " finished_at, created_at FROM public.triage_modules"
                " WHERE case_id = %s ORDER BY created_at, id",
                (case_id,),
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.error(json.dumps({"event": "TRIAGE_LIST_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao listar o plano de triagem")
    return success_response(200, f"{len(rows)} módulos de triagem", [_serialize_module(r) for r in rows])


@require_user
@require_writer
def run_triage(event, context):
    """Executa a triagem do caso (SP3): roda os módulos (mock), grava evidências
    (external_queries_cache + provider_results), atualiza status e a timeline."""
    user = event["user"]
    org = user["organization_id"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute("SELECT 1 FROM public.cases WHERE id = %s", (case_id,))
            if cur.fetchone() is None:
                return error_response(404, "Caso não encontrado")
            result = run_case_triage(cur, org, case_id, user["user_id"])
    except Exception as e:
        logger.error(json.dumps({"event": "TRIAGE_RUN_ERROR", "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao executar a triagem")
    logger.info(json.dumps({"event": "TRIAGE_EXECUTED", "case_id": case_id,
                            "modules": result["modules_executed"]}))
    return success_response(200, "Triagem executada", result)
