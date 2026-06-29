"""Handlers Lambda de `documents` (migração FastAPI→Serverless, Fase 4).

`public.documents` tem RLS por **`uploaded_by`** (dono do documento) + auditoria por
trigger → usa `tenant_tx` (fixa app.user_id/role). Upload e download usam URLs
S3 pré-assinadas (o arquivo não passa pela Lambda). O upload só é permitido para um
`case` VISÍVEL ao usuário (verificado atômico na mesma transação). Escrita exige
papel writer (admin/analyst); leitura, qualquer autenticado (a RLS filtra).
"""
import json
import logging
import uuid

from pydantic import ValidationError

from src.schemas.document_schemas import DocumentUploadSchema
from src.services.database import tenant_tx
from src.services.storage import storage_service
from src.utils.context import require_user, require_writer
from src.utils.helpers import error_response, success_response
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


class _CaseNotVisible(Exception):
    """O case referenciado não existe ou não é visível ao usuário (RLS)."""


def _fmt(err: ValidationError) -> str:
    return ", ".join(
        f"{(e['loc'][0] if e['loc'] else '?')}: {e['msg']}" for e in err.errors()
    )


def _parse_body(event):
    try:
        return json.loads(event.get("body") or "{}"), None
    except json.JSONDecodeError:
        return None, error_response(400, "Corpo JSON inválido")


def _valid_uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


@require_user
@require_writer
def upload_document(event, context):
    user = event["user"]
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = DocumentUploadSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    case_id = _valid_uuid(data.case_id)
    if not case_id:
        return error_response(400, "case_id inválido")

    doc_id = str(uuid.uuid4())
    s3_key = storage_service.build_key(case_id, doc_id, data.file_name)
    s3_url = storage_service.object_url(s3_key)

    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            # Atômico: só insere se o case for VISÍVEL ao usuário (RLS de cases).
            cur.execute(
                "INSERT INTO public.documents"
                " (id, case_id, s3_url, s3_path, file_name, file_type,"
                "  file_size_bytes, file_hash, document_classification,"
                "  ocr_status, extraction_status, uploaded_by)"
                " SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', 'pending', %s"
                " WHERE EXISTS (SELECT 1 FROM public.cases WHERE id = %s)"
                " RETURNING id, created_at",
                (doc_id, case_id, s3_url, s3_key, data.file_name, data.file_type,
                 data.file_size_bytes, data.file_hash, data.document_classification,
                 user["user_id"], case_id),
            )
            row = cur.fetchone()
            if row is None:
                raise _CaseNotVisible()
    except _CaseNotVisible:
        return error_response(404, "Caso não encontrado ou sem acesso")
    except Exception as e:
        logger.error(json.dumps({"event": "DOCUMENT_UPLOAD_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao registrar documento")

    upload_url = storage_service.presign_put(s3_key, _content_type(data.file_type))
    logger.info(json.dumps({"event": "DOCUMENT_UPLOADED", "document_id": doc_id,
                            "case_id": case_id}))
    return success_response(201, "Documento registrado; use a URL para enviar o arquivo", {
        "document_id": doc_id,
        "upload_url": upload_url,
        "s3_path": s3_key,
        "expires_in": storage_service.expires,
        "created_at": str(row["created_at"]),
    })


@require_user
def get_document(event, context):
    user = event["user"]
    doc_id = _valid_uuid((event.get("pathParameters") or {}).get("docId"))
    if not doc_id:
        return error_response(400, "docId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            cur.execute(
                "SELECT id, case_id, s3_path, file_name, file_type, file_size_bytes,"
                " file_hash, ocr_status, extraction_status, document_classification,"
                " created_at FROM public.documents WHERE id = %s",
                (doc_id,),
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "DOCUMENT_GET_ERROR",
                                 "error": type(e).__name__}))
        return error_response(500, "Erro ao obter documento")
    if not row:
        return error_response(404, "Documento não encontrado")

    download_url = storage_service.presign_get(row["s3_path"]) if row["s3_path"] else None
    data = _serialize(row)
    data["download_url"] = download_url
    data["expires_in"] = storage_service.expires
    return success_response(200, "Documento encontrado", data)


def _content_type(file_type: str) -> str:
    return {
        "pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "tiff": "image/tiff", "txt": "text/plain",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(file_type, "application/octet-stream")


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "case_id": str(row["case_id"]),
        "file_name": row["file_name"],
        "file_type": row.get("file_type"),
        "file_size_bytes": row.get("file_size_bytes"),
        "file_hash": row.get("file_hash"),
        "ocr_status": row.get("ocr_status"),
        "extraction_status": row.get("extraction_status"),
        "document_classification": row.get("document_classification"),
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
    }
