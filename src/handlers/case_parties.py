"""Handlers de leitura das partes do caso (aba "Partes").

GET /cases/{caseId}/parties — lista as partes com PII MASCARADA (LGPD-01):
documento/e-mail/telefone só em formato ``*_masked``; nunca o valor cru.
Espelha o ``serialize_case_party`` do legaltech-aws.
"""
import json
import logging

from psycopg2.extras import Json

from src.services.case_lifecycle import (
    CaseFinalized,
    CaseNotVisible,
    assert_case_writable,
)
from src.services.database import tenant_tx
from src.utils.context import (CallerRevoked, assert_active_writer, require_user,
                               require_writer)
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import parse_json_body as _parse_body, valid_uuid as _valid_uuid
from src.utils.pii import mask_document, mask_email, mask_phone
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()

# chaves de PII que NUNCA voltam no metadata exposto
_PII_KEYS = {"email", "phone", "document", "document_number", "cpf", "cnpj", "rg"}
# allowlist de campos aceitos no metadata da parte (evita PII/campos arbitrários)
_META_ALLOWLIST = ("person_type", "document_type", "notes", "email", "phone")


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
            cur.execute("SELECT 1 FROM public.cases WHERE id = %s AND deleted_at IS NULL", (case_id,))
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
        logger.error(json.dumps({"event": "CASE_PARTIES_LIST_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao listar partes do caso")
    return success_response(200, f"{len(rows)} partes encontradas",
                            [_serialize_party(r) for r in rows])


_RETURN_COLS = (" RETURNING id, case_id, party_type, name, document, metadata,"
                " created_at, updated_at")


@require_user
@require_writer
def create_case_party(event, context):
    """Cria uma parte no caso (PII volta mascarada). Registra timeline party_added."""
    user = event["user"]
    org = user["organization_id"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    # Type-safe: campos com tipo errado (ex.: name:123, metadata:"x") -> 400, nunca 500
    # (achado do pentest 2026-07-09; antes .strip()/.get() em não-str/não-dict estourava).
    name = body.get("name")
    party_type = body.get("party_type")
    document = body.get("document")
    if not isinstance(name, str) or not isinstance(party_type, str) \
            or (document is not None and not isinstance(document, str)):
        return error_response(400, "name, party_type e document devem ser texto")
    name, party_type = name.strip(), party_type.strip()
    if not name or not party_type:
        return error_response(400, "name e party_type são obrigatórios")
    raw_md = body.get("metadata")
    if not isinstance(raw_md, dict):
        raw_md = {}
    metadata = {"created_by": user["user_id"]}
    for k in _META_ALLOWLIST:  # allowlist estrita (não aceita metadata arbitrário)
        v = body.get(k, raw_md.get(k))
        if v not in (None, ""):
            metadata[k] = v
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            # SEC-01: fecha a janela de revogação (~2h) do token antes de escrever.
            assert_active_writer(cur, user["user_id"], org)
            # CVS-007: 404 se inexistente/deletado; 409 se finalizado
            assert_case_writable(cur, case_id)
            cur.execute(
                "INSERT INTO public.case_parties"
                " (organization_id, case_id, party_type, name, document, metadata)"
                " VALUES (%s,%s,%s,%s,%s,%s)" + _RETURN_COLS,
                (org, case_id, party_type, name, body.get("document"), Json(metadata)))
            row = cur.fetchone()
            cur.execute(
                "INSERT INTO public.timeline_events"
                " (organization_id, case_id, event_type, title, description, actor)"
                " VALUES (%s,%s,'party_added','Parte adicionada','Parte registrada no caso.','user')",
                (org, case_id))
    except CallerRevoked:
        return error_response(403, "Permissão de escrita revogada")
    except CaseNotVisible:
        return error_response(404, "Caso não encontrado")
    except CaseFinalized:
        return error_response(409, "caso finalizado não aceita novas partes")
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_PARTY_CREATE_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao criar parte")
    return success_response(201, "Parte criada com sucesso", _serialize_party(row))


@require_user
@require_writer
def update_case_party(event, context):
    """Atualiza uma parte do caso (PII volta mascarada)."""
    user = event["user"]
    org = user["organization_id"]
    pp = event.get("pathParameters") or {}
    case_id = _valid_uuid(pp.get("caseId"))
    party_id = _valid_uuid(pp.get("partyId"))
    if not case_id or not party_id:
        return error_response(400, "ids inválidos")
    body, err = _parse_body(event)
    if err:
        return err
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            # SEC-01: fecha a janela de revogação (~2h) do token antes de escrever.
            assert_active_writer(cur, user["user_id"], org)
            # CVS-007: 404 se caso inexistente/deletado; 409 se finalizado
            assert_case_writable(cur, case_id)
            cur.execute(
                "SELECT metadata FROM public.case_parties"
                " WHERE id = %s AND case_id = %s AND deleted_at IS NULL", (party_id, case_id))
            cur_row = cur.fetchone()
            if not cur_row:
                return error_response(404, "Parte não encontrada")
            fields, values = [], []
            if body.get("name"):
                fields.append("name = %s")
                values.append(body["name"].strip())
            if body.get("party_type"):
                fields.append("party_type = %s")
                values.append(body["party_type"].strip())
            if "document" in body:
                fields.append("document = %s")
                values.append(body["document"])
            md = dict(cur_row["metadata"] or {})
            raw_md = body.get("metadata") or {}
            for k in _META_ALLOWLIST:  # allowlist estrita
                if k in body or k in raw_md:
                    v = body.get(k, raw_md.get(k))
                    if v in (None, ""):
                        md.pop(k, None)
                    else:
                        md[k] = v
            fields.append("metadata = %s")
            values.append(Json(md))
            fields.append("updated_at = NOW()")
            values.extend([party_id, case_id])
            cur.execute(
                f"UPDATE public.case_parties SET {', '.join(fields)}"
                " WHERE id = %s AND case_id = %s" + _RETURN_COLS, tuple(values))
            row = cur.fetchone()
    except CallerRevoked:
        return error_response(403, "Permissão de escrita revogada")
    except CaseNotVisible:
        return error_response(404, "Caso não encontrado")
    except CaseFinalized:
        return error_response(409, "caso finalizado não aceita alteração de partes")
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_PARTY_UPDATE_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao atualizar parte")
    return success_response(200, "Parte atualizada com sucesso", _serialize_party(row))
