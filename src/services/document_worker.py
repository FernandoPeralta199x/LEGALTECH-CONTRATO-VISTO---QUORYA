"""Worker de processamento de documento (SP1) — consome um DocumentProcessingJob e
roda a ingestão SP2 de forma idempotente, espelhando workers/document_processing da
referência. Opera no cursor de uma transação tenant_tx (RLS por org).

Idempotência em três camadas:
  - por job (organization_id, job_id) via agent_executions: completed -> não refaz;
    running/retrying -> em andamento; failed -> retry só dentro do limite de tentativas;
  - por documento: pg_advisory_xact_lock serializa jobs do mesmo documento; se já há
    chunks (double-check após o lock), pula ('document_already_processed');
  - tratamento de falha em duas classes:
      * DETERMINÍSTICA (documento/caso ausente, OCR sem texto): marca 'failed' e dá ACK
        — reprocessar não resolve;
      * TRANSITÓRIA (DB/embeddings/infra): a exceção PROPAGA — o chamador (tenant_tx)
        faz rollback (desfaz chunks parciais) e a SQS reentrega até o DLQ (retry real).
"""
import logging

from src.schemas.queue_schemas import DocumentProcessingJob, WorkerResult
from src.services import agent_executions as ae
from src.services.document_ingestion import (
    DocumentNotFound,
    OcrFailed,
    ingest_document as _ingest_document,
)

logger = logging.getLogger()

DEFAULT_MAX_ATTEMPTS = 3

# erro determinístico do conteúdo/estado (não adianta reprocessar): marca failed + ack
_DETERMINISTIC = (LookupError, DocumentNotFound, OcrFailed)


def _result(job, status, reason=None) -> WorkerResult:
    return WorkerResult(job_id=job.job_id, document_id=job.document_id,
                        status=status, reason=reason)


def _timeline(cur, org, case_id, event_type, title, description, *, guard=False) -> None:
    """Registra um evento na timeline do caso (auditoria semântica). Com guard=True,
    usa SAVEPOINT para não abortar a transação caso o caso não exista (FK)."""
    sql = ("INSERT INTO public.timeline_events"
           " (organization_id, case_id, event_type, title, description, actor)"
           " VALUES (%s,%s,%s,%s,%s,'system')")
    params = (str(org), str(case_id), event_type, title, description)
    if not guard:
        cur.execute(sql, params)
        return
    cur.execute("SAVEPOINT sp_tl")
    try:
        cur.execute(sql, params)
        cur.execute("RELEASE SAVEPOINT sp_tl")
    except Exception:  # noqa: BLE001 — timeline best-effort; não converte falha em transitória
        cur.execute("ROLLBACK TO SAVEPOINT sp_tl")


def process_job(cur, org, job: DocumentProcessingJob, *,
                max_attempts: int = DEFAULT_MAX_ATTEMPTS, ingest=_ingest_document) -> WorkerResult:
    """Processa um job idempotentemente. Não faz commit/rollback (responsabilidade do
    chamador). Falhas transitórias PROPAGAM para reentrega/DLQ via SQS."""
    execution = ae.get_by_job_id(cur, job.job_id)

    # idempotência por job
    if execution and ae.is_completed(execution["status"]):
        return _result(job, "completed", "duplicate_completed")
    if execution and ae.is_busy(execution["status"]):
        return _result(job, "skipped", "job_already_running")

    if execution is None:
        execution = ae.create_queued(cur, org, job)
    elif ae.can_retry_attempt(current_status=execution["status"], current_attempt=execution["attempt"],
                              requested_attempt=job.attempt, max_attempts=max_attempts):
        execution = ae.mark_retrying(cur, execution["id"], job.attempt)
    elif execution["status"] == ae.FAILED:
        return _result(job, "failed", "retry_not_allowed")

    if ae.max_attempts_exceeded(job_attempt=job.attempt, max_attempts=max_attempts):
        ae.mark_failed(cur, execution["id"], "MaxAttemptsExceeded")
        return _result(job, "failed", "MaxAttemptsExceeded")

    # concorrência: serializa jobs do mesmo documento (lock liberado no commit/rollback)
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(job.document_id),))

    try:
        cur.execute("SELECT id, case_id FROM public.documents WHERE id = %s", (str(job.document_id),))
        doc = cur.fetchone()
        if doc is None:
            raise DocumentNotFound("document_not_found")
        cur.execute("SELECT 1 FROM public.cases WHERE id = %s", (str(job.case_id),))
        if cur.fetchone() is None:
            raise LookupError("case_not_found")
        if str(doc["case_id"]) != str(job.case_id):
            raise LookupError("document_not_for_case")

        # dedupe por documento (double-checked após o advisory lock)
        cur.execute("SELECT 1 FROM public.document_chunks WHERE document_id = %s LIMIT 1",
                    (str(job.document_id),))
        if cur.fetchone() is not None:
            ae.mark_skipped(cur, execution["id"], "document_already_processed")
            _timeline(cur, org, job.case_id, "document_processing_skipped",
                      "Processamento ignorado", "Documento já processado (chunks existentes).")
            return _result(job, "skipped", "document_already_processed")

        execution = ae.mark_running(cur, execution["id"], job.attempt)
        _timeline(cur, org, job.case_id, "document_processing_started",
                  "Processamento iniciado", "Worker iniciou a ingestão do documento.")
        result = ingest(cur, org, job.document_id)
        ae.mark_completed(cur, execution["id"], {
            "status": result.get("status"), "chunk_count": result.get("chunk_count"),
            "embedding_count": result.get("embedding_count")})
        _timeline(cur, org, job.case_id, "document_processed", "Documento processado",
                  f"Ingestão concluída ({result.get('chunk_count')} chunks,"
                  f" {result.get('embedding_count')} embeddings).")
        return _result(job, "completed")
    except _DETERMINISTIC as exc:
        # falha determinística: registra failed + timeline (guard) e ACK (sem retry)
        ae.mark_failed(cur, execution["id"], exc.__class__.__name__)
        _timeline(cur, org, job.case_id, "document_processing_failed", "Falha no processamento",
                  f"Falha determinística: {exc.__class__.__name__}.", guard=True)
        logger.info('{"event":"DOC_JOB_FAILED_DET","error":"%s"}' % exc.__class__.__name__)
        return _result(job, "failed", exc.__class__.__name__)
    # exceções transitórias (DB/embeddings/infra) NÃO são capturadas aqui: propagam para
    # o tenant_tx (rollback desfaz chunks parciais) e o handler reporta batchItemFailure.
