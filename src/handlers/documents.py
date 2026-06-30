"""Handlers Lambda de `documents` (migração FastAPI→Serverless, Fase 4).

`public.documents` tem RLS por **`organization_id`** (multi-tenant V2, FORCE RLS) +
auditoria por trigger → usa `tenant_tx` (fixa app.user_id/role/organization_id).
Upload e download usam URLs
S3 pré-assinadas (o arquivo não passa pela Lambda). O upload só é permitido para um
`case` VISÍVEL ao usuário (verificado atômico na mesma transação). Escrita exige
papel writer (admin/analyst); leitura, qualquer autenticado (a RLS filtra).
"""
import json
import logging
import uuid

from pydantic import ValidationError

from src.schemas.document_schemas import DocumentUploadSchema
from src.schemas.queue_schemas import DocumentProcessingJob, EnqueueDocumentProcessingResult
from src.services.database import tenant_tx
from src.services.document_ingestion import DocumentNotFound, OcrFailed, ingest_document
from src.services.document_worker import process_job
from src.services.queue import create_queue_client
from src.services.storage import storage_service
from src.utils.context import require_user, require_writer
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import fmt_validation_error as _fmt, parse_json_body as _parse_body, valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()

# FE4-26-B1: documento só é acessível se o caso pai estiver ativo (não arquivado).
# Soft-delete do caso (deleted_at) torna seus documentos inalcançáveis por docId.
_ACTIVE_CASE = (" AND EXISTS (SELECT 1 FROM public.cases c"
                " WHERE c.id = public.documents.case_id AND c.deleted_at IS NULL)")


class _CaseNotVisible(Exception):
    """O case referenciado não existe ou não é visível ao usuário (RLS)."""


class _CaseFinalized(Exception):
    """O case existe mas está finalizado (completed/closed) — não aceita upload."""


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
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # Atômico: só insere se o case for VISÍVEL ao usuário (RLS de cases).
            cur.execute(
                "INSERT INTO public.documents"
                " (id, organization_id, case_id, s3_url, s3_path, file_name, file_type,"
                "  file_size_bytes, file_hash, document_classification,"
                "  ocr_status, extraction_status, uploaded_by)"
                " SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', 'pending', %s"
                " WHERE EXISTS (SELECT 1 FROM public.cases WHERE id = %s"
                "               AND status NOT IN ('completed','closed'))"
                " RETURNING id, created_at",
                (doc_id, user["organization_id"], case_id, s3_url, s3_key, data.file_name,
                 data.file_type, data.file_size_bytes, data.file_hash,
                 data.document_classification, user["user_id"], case_id),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute("SELECT 1 FROM public.cases WHERE id = %s", (case_id,))
                if cur.fetchone() is None:
                    raise _CaseNotVisible()
                raise _CaseFinalized()
    except _CaseNotVisible:
        return error_response(404, "Caso não encontrado ou sem acesso")
    except _CaseFinalized:
        return error_response(409, "caso finalizado não aceita novos documentos")
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
@require_writer
def process_document(event, context):
    """Ingestão do documento (SP2), idempotente: OCR -> chunking -> embeddings,
    gravando document_chunks/document_embeddings. Mock-first (Textract/Bedrock na
    fase AWS). Limpa-e-regera: apaga chunks/embeddings anteriores antes de gerar.
    """
    user = event["user"]
    org = user["organization_id"]
    doc_id = _valid_uuid((event.get("pathParameters") or {}).get("docId"))
    if not doc_id:
        return error_response(400, "docId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            res = ingest_document(cur, org, doc_id)
    except DocumentNotFound:
        return error_response(404, "Documento não encontrado")
    except OcrFailed:
        return error_response(502, "Falha na extração de texto (OCR)")
    except Exception as e:
        logger.error(json.dumps({"event": "DOCUMENT_PROCESS_ERROR", "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao processar documento")

    logger.info(json.dumps({"event": "DOCUMENT_PROCESSED", "document_id": doc_id,
                            "chunks": res["chunk_count"]}))
    return success_response(200, "Documento processado", {
        "document_id": doc_id,
        "status": "done",
        # contrato compatível com a execução síncrona (force-reprocess):
        "job_id": f"sync-{doc_id}",
        "queue_backend": "inline",
        "pages": res["pages"],
        "chunks": res["chunk_count"],
        "embeddings": res["embedding_count"],
        "ocr_source": res["ocr_source"],
        "embedding_model": res["embedding_model"],
    })


@require_user
@require_writer
def enqueue_processing(event, context):
    """Enfileira o processamento do documento (SP1). Na AWS publica na SQS (worker
    assíncrono); em dev (inline) cria a execução e processa de forma síncrona —
    em ambos os casos a resposta é 'queued' (contrato EnqueueProcessingResult)."""
    user = event["user"]
    org = user["organization_id"]
    doc_id = _valid_uuid((event.get("pathParameters") or {}).get("docId"))
    if not doc_id:
        return error_response(400, "docId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute("SELECT id, case_id FROM public.documents WHERE id = %s" + _ACTIVE_CASE,
                        (doc_id,))
            doc = cur.fetchone()
            if not doc:
                return error_response(404, "Documento não encontrado")
            job = DocumentProcessingJob(
                organization_id=org, case_id=doc["case_id"], document_id=doc_id,
                requested_by=user["user_id"], metadata={"source": "api"})
            client = create_queue_client()
            client.publish(job)  # SQS: envia a mensagem; inline: no-op
            if client.backend == "inline":
                # dev: sem SQS, processa o job de forma síncrona (idempotente)
                process_job(cur, org, job)
            result = EnqueueDocumentProcessingResult(
                job_id=job.job_id, queue_backend=client.backend, document_id=doc_id)
    except Exception as e:
        logger.error(json.dumps({"event": "DOCUMENT_ENQUEUE_ERROR", "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao enfileirar o processamento")

    logger.info(json.dumps({"event": "DOCUMENT_ENQUEUED", "document_id": doc_id,
                            "job_id": str(job.job_id), "backend": client.backend}))
    return success_response(202, "Processamento enfileirado", result.model_dump(mode="json"))


@require_user
def get_document(event, context):
    user = event["user"]
    doc_id = _valid_uuid((event.get("pathParameters") or {}).get("docId"))
    if not doc_id:
        return error_response(400, "docId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(
                "SELECT id, case_id, s3_path, file_name, file_type, file_size_bytes,"
                " file_hash, ocr_status, extraction_status, uploaded_by,"
                " created_at FROM public.documents WHERE id = %s" + _ACTIVE_CASE,
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
    # shape V2 esperado pelo frontend (mapBackendDocument) + url de download
    data = _serialize_v2(row)
    data["download_url"] = download_url
    data["expires_in"] = storage_service.expires
    return success_response(200, "Documento encontrado", data)


@require_user
@require_writer
def update_document(event, context):
    """Atualização parcial do documento (PATCH). Campos suportados: ``filename`` (rename)
    e ``status`` (V2 -> ocr_status legado). Escrita exige papel writer; a RLS filtra a org."""
    user = event["user"]
    doc_id = _valid_uuid((event.get("pathParameters") or {}).get("docId"))
    if not doc_id:
        return error_response(400, "docId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    sets, vals = [], []
    if "filename" in body and body["filename"]:
        sets.append("file_name = %s")
        vals.append(body["filename"])
    if "status" in body and body["status"]:
        if body["status"] not in _DOC_STATUS_REV:
            return error_response(400, "status inválido (use: pending_upload, processed, failed)")
        sets.append("ocr_status = %s")
        vals.append(_DOC_STATUS_REV[body["status"]])
    if not sets:
        return error_response(400, "Nenhum campo atualizável (use 'filename' e/ou 'status')")
    vals.append(doc_id)
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(
                f"UPDATE public.documents SET {', '.join(sets)} WHERE id = %s" + _ACTIVE_CASE +
                " RETURNING id, case_id, file_name, file_type, file_size_bytes, file_hash,"
                " ocr_status, uploaded_by, created_at", tuple(vals))
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "DOCUMENT_UPDATE_ERROR", "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao atualizar documento")
    if not row:
        return error_response(404, "Documento não encontrado")
    return success_response(200, "Documento atualizado", _serialize_v2(row))


@require_user
def get_document_download_url(event, context):
    """URL pré-assinada de download (shape DocumentDownloadUrl do frontend)."""
    user = event["user"]
    doc_id = _valid_uuid((event.get("pathParameters") or {}).get("docId"))
    if not doc_id:
        return error_response(400, "docId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute("SELECT s3_path FROM public.documents WHERE id = %s" + _ACTIVE_CASE,
                        (doc_id,))
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "DOCUMENT_DOWNLOAD_URL_ERROR",
                                 "error": type(e).__name__}))
        return error_response(500, "Erro ao gerar URL de download")
    if not row:
        return error_response(404, "Documento não encontrado")
    if not row["s3_path"]:
        return error_response(409, "Documento ainda não possui arquivo para download")
    return success_response(200, "URL de download", {
        "url": storage_service.presign_get(row["s3_path"]),
        "expires_in_seconds": storage_service.expires,
        "method": "GET",
    })


@require_user
def list_documents(event, context):
    """Lista documentos da organização no shape V2 esperado pelo frontend.

    Filtro opcional por ``case_id`` (query). A RLS já restringe à organização.
    Mapeia o schema legado de ``documents`` para o contrato V2 do frontend.
    """
    user = event["user"]
    params = event.get("queryStringParameters") or {}
    case_id = None
    if params.get("case_id"):
        case_id = _valid_uuid(params["case_id"])
        if not case_id:
            return error_response(400, "case_id inválido")
    # sempre exclui documentos de casos arquivados (soft-delete) — FE4-26-B1
    conditions = ["EXISTS (SELECT 1 FROM public.cases c WHERE c.id = public.documents.case_id"
                  " AND c.deleted_at IS NULL)"]
    args = []
    if case_id:
        conditions.append("case_id = %s")
        args.append(case_id)
    if params.get("status"):
        # filtro V2 do frontend -> ocr_status legado; status não mapeável é erro de contrato
        if params["status"] not in _DOC_STATUS_REV:
            return error_response(400, "status inválido (use: pending_upload, processed, failed)")
        args.append(_DOC_STATUS_REV[params["status"]])
        conditions.append("ocr_status = %s")
    sql = ("SELECT id, case_id, file_name, file_type, file_size_bytes, file_hash,"
           " ocr_status, uploaded_by, created_at FROM public.documents")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY created_at DESC LIMIT 500"
    args = tuple(args)
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
    except Exception as e:
        logger.error(json.dumps({"event": "DOCUMENT_LIST_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao listar documentos")
    return success_response(200, f"{len(rows)} documentos", [_serialize_v2(r) for r in rows])


# ocr_status (legado) -> status do documento V2 esperado pelo frontend
_DOC_STATUS_MAP = {"pending": "pending_upload", "done": "processed"}
# inverso: status V2 (filtro/PATCH do frontend) -> ocr_status legado. Conjunto fechado:
# só estes status mapeiam para a coluna ocr_status; outros são rejeitados (evita filtro
# vazio silencioso e poluição de ocr_status) — FE4-26-B3.
_DOC_STATUS_REV = {"pending_upload": "pending", "processed": "done", "failed": "failed"}


def _serialize_v2(row) -> dict:
    """Schema legado de documents -> shape V2 do frontend (filename/content_type/status)."""
    created = str(row["created_at"]) if row.get("created_at") else None
    raw_status = row.get("ocr_status") or "pending_upload"
    return {
        "id": str(row["id"]),
        "case_id": str(row["case_id"]),
        "filename": row["file_name"],
        "content_type": _content_type(row.get("file_type") or ""),
        "size_bytes": row.get("file_size_bytes") or 0,
        "file_hash": row.get("file_hash"),
        "status": _DOC_STATUS_MAP.get(raw_status, raw_status),
        "uploaded_by": str(row["uploaded_by"]) if row.get("uploaded_by") else None,
        "uploaded_at": created,
        "metadata": {},
        "created_at": created,
        "updated_at": created,
    }


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
