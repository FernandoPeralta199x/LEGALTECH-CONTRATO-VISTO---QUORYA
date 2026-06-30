"""Handlers Lambda de `clients` (migração FastAPI→Serverless; multi-tenant V2).

`public.clients` agora é **por organização** (RLS por `organization_id` + FORCE).
Usa `tenant_tx` (contexto da org do usuário). Leitura para qualquer usuário
autenticado da org; escrita (create/update/delete) apenas para papéis de escrita
(admin/analyst) via `require_writer`. `viewer` é somente leitura. Nunca vaza
`str(e)` nem loga PII (document_number/legal_name).
"""
import json
import logging

import psycopg2
from pydantic import ValidationError

from src.schemas.client_schemas import ClientCreateSchema, ClientUpdateSchema
from src.services.database import tenant_tx
from src.utils.context import require_user, require_writer
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import fmt_validation_error as _fmt, parse_json_body as _parse_body, parse_pagination as _paginate, valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


@require_user
@require_writer
def create_client(event, context):
    user = event["user"]
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = ClientCreateSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(
                "INSERT INTO public.clients"
                " (organization_id, legal_name, document_type, document_number, email, phone,"
                "  address_street, address_city, address_state, address_zip)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id, legal_name, document_type, document_number, email, phone,"
                " address_street, address_city, address_state, address_zip, status,"
                " created_at, updated_at",
                (user["organization_id"], data.legal_name, data.document_type,
                 data.document_number, data.email, data.phone, data.address_street,
                 data.address_city, data.address_state, data.address_zip),
            )
            row = cur.fetchone()
    except psycopg2.errors.UniqueViolation:
        return error_response(409, "document_number já cadastrado")
    except Exception as e:
        logger.error(json.dumps({"event": "CLIENT_CREATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao criar cliente")

    logger.info(json.dumps({"event": "CLIENT_CREATED", "client_id": str(row["id"])}))
    return success_response(201, "Cliente criado com sucesso", _serialize(row, user["role"]))


@require_user
def get_client(event, context):
    user = event["user"]
    client_id = _valid_uuid((event.get("pathParameters") or {}).get("clientId"))
    if not client_id:
        return error_response(400, "clientId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(_SELECT_COLS + " WHERE id = %s", (client_id,))
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "CLIENT_GET_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao obter cliente")
    if not row:
        return error_response(404, "Cliente não encontrado")
    return success_response(200, "Cliente encontrado", _serialize(row, user["role"]))


@require_user
def list_clients(event, context):
    user = event["user"]
    params = event.get("queryStringParameters") or {}
    pag, perr = _paginate(params)
    if perr:
        return perr
    page, page_size, offset = pag
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(
                _SELECT_COLS + " WHERE status = 'active'"
                " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (page_size, offset),
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.error(json.dumps({"event": "CLIENT_LIST_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao listar clientes")
    return success_response(
        200, f"{len(rows)} clientes encontrados",
        [_serialize(r, user["role"]) for r in rows]
    )


@require_user
@require_writer
def update_client(event, context):
    user = event["user"]
    client_id = _valid_uuid((event.get("pathParameters") or {}).get("clientId"))
    if not client_id:
        return error_response(400, "clientId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = ClientUpdateSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    fields, values = [], []
    for col in ("legal_name", "email", "phone", "address_street",
                "address_city", "address_state", "address_zip"):
        val = getattr(data, col)
        if val is not None:
            fields.append(f"{col} = %s")
            values.append(val)
    if data.status is not None:
        # mantém is_active coerente com status (delete usa ambos)
        fields.append("status = %s")
        values.append(data.status)
        fields.append("is_active = %s")
        values.append(data.status == "active")
    if not fields:
        return error_response(400, "Nenhum campo para atualizar")
    fields.append("updated_at = NOW()")
    values.append(client_id)

    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(
                f"UPDATE public.clients SET {', '.join(fields)} WHERE id = %s",
                tuple(values),
            )
            if not cur.rowcount:
                return error_response(404, "Cliente não encontrado")
            cur.execute(_SELECT_COLS + " WHERE id = %s", (client_id,))
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "CLIENT_UPDATE_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao atualizar cliente")
    logger.info(json.dumps({"event": "CLIENT_UPDATED", "client_id": client_id}))
    return success_response(200, "Cliente atualizado com sucesso", _serialize(row, user["role"]))


@require_user
@require_writer
def delete_client(event, context):
    user = event["user"]
    client_id = _valid_uuid((event.get("pathParameters") or {}).get("clientId"))
    if not client_id:
        return error_response(400, "clientId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(
                "UPDATE public.clients SET status = 'inactive', is_active = false,"
                " updated_at = NOW() WHERE id = %s",
                (client_id,),
            )
            updated = cur.rowcount
    except Exception as e:
        logger.error(json.dumps({"event": "CLIENT_DELETE_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao deletar cliente")
    if not updated:
        return error_response(404, "Cliente não encontrado")
    logger.info(json.dumps({"event": "CLIENT_DELETED", "client_id": client_id}))
    return success_response(200, "Cliente desativado com sucesso")


_SELECT_COLS = (
    "SELECT id, legal_name, document_type, document_number, email, phone,"
    " address_street, address_city, address_state, address_zip, status,"
    " created_at, updated_at"
    " FROM public.clients"
)


def _mask_document(num: str) -> str:
    """Mascara o documento mantendo só os 4 últimos dígitos (PII p/ viewer)."""
    if not num or len(num) <= 4:
        return num
    return "*" * (len(num) - 4) + num[-4:]


def _serialize(row, role="admin") -> dict:
    document = row["document_number"]
    email = row.get("email")
    phone = row.get("phone")
    address = {
        "street": row.get("address_street"),
        "city": row.get("address_city"),
        "state": row.get("address_state"),
        "zip": row.get("address_zip"),
    }
    if role == "viewer":  # viewer vê PII reduzida (LGPD): documento mascarado e
        document = _mask_document(document)  # sem dados de contato/endereço
        email = phone = address = None
    # Shape alinhado ao contrato do frontend (BackendClient): aliases name/document/
    # document_masked/metadata/updated_at, mantendo os campos legados.
    return {
        "id": str(row["id"]),
        "name": row["legal_name"],
        "display_name": row["legal_name"],
        "legal_name": row["legal_name"],
        "document_type": row["document_type"],
        "document": document,
        "document_number": document,
        "document_masked": _mask_document(row["document_number"]),
        "email": email,
        "phone": phone,
        "address": address,
        "metadata": {},
        "status": row["status"],
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
        "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
    }
