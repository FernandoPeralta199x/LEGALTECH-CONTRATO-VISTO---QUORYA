"""Handlers de leitura das partes do caso (aba "Partes").

GET /cases/{caseId}/parties — lista as partes com PII MASCARADA (LGPD-01):
documento/e-mail/telefone só em formato ``*_masked``; nunca o valor cru.
Espelha o ``serialize_case_party`` do legaltech-aws.
"""
import json
import logging

from src.services.database import tenant_tx
from src.utils.context import require_user
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import valid_uuid as _valid_uuid
from src.utils.pii import mask_document, mask_email, mask_phone
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()

# chaves de PII que NUNCA voltam no metadata exposto
_PII_KEYS = {"email", "phone", "document", "document_number", "cpf", "cnpj", "rg"}


def _serialize_party(row) -> dict:
    metadata = dict(row["metadata"] or {})
    raw_email = metadata.get("email") if isinstance(metadata.get("email"), str) else None
    raw_phone = metadata.get("phone") if isinstance(metadata.get("phone"), str) else None
    safe_metadata = {k: v for k, v in metadata.items() if k not in _PII_KEYS}
    return {
        "id": str(row["id"]),
        "case_id": str(row["case_id"]),
        "party_type": row["party_type"],
        "name": row["name"],
        "document": None,
        "document_masked": mask_document(row["document"]),
        "email": None,
        "email_masked": mask_email(raw_email) if raw_email else None,
        "phone": None,
        "phone_masked": mask_phone(raw_phone) if raw_phone else None,
        "notes": metadata.get("notes") if isinstance(metadata.get("notes"), str) else None,
        "metadata": safe_metadata,
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
        "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
    }


@require_user
def list_case_parties(event, context):
    """Lista as partes do caso (PII mascarada). 404 se o caso não é visível à org."""
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
                "SELECT id, case_id, party_type, name, document, metadata,"
                " created_at, updated_at FROM public.case_parties"
                " WHERE case_id = %s AND deleted_at IS NULL ORDER BY created_at",
                (case_id,),
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_PARTIES_LIST_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao listar partes do caso")
    return success_response(200, f"{len(rows)} partes encontradas",
                            [_serialize_party(r) for r in rows])
