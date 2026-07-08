"""Handlers de leitura da linha do tempo do caso (aba "Timeline").

GET /cases/{caseId}/timeline — lista os eventos do caso (mais recentes primeiro).
"""
import json
import logging

from src.services.database import tenant_tx
from src.utils.context import require_user
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


def _serialize_event(row) -> dict:
    return {
        "id": str(row["id"]),
        "case_id": str(row["case_id"]),
        "event_type": row["event_type"],
        "title": row["title"],
        "description": row["description"],
        "actor": row["actor"],
        "actor_id": str(row["actor_id"]) if row["actor_id"] else None,
        "payload": row["payload"] or {},
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
    }


@require_user
def list_timeline(event, context):
    """Lista os eventos da timeline do caso. 404 se o caso não é visível à org."""
    user = event["user"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute("SELECT 1 FROM public.cases WHERE id = %s AND deleted_at IS NULL", (case_id,))
            if cur.fetchone() is None:
                return error_response(404, "Caso não encontrado")
            cur.execute(
                "SELECT id, case_id, event_type, title, description, actor, actor_id,"
                " payload, created_at FROM public.timeline_events"
                " WHERE case_id = %s ORDER BY created_at DESC, id DESC LIMIT 500",
                (case_id,),
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.error(json.dumps({"event": "TIMELINE_LIST_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao listar a timeline do caso")
    return success_response(200, f"{len(rows)} eventos encontrados",
                            [_serialize_event(r) for r in rows])
