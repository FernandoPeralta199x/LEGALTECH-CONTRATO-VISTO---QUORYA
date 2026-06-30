"""Worker de processamento de documento (SP1) — consome um DocumentProcessingJob e
roda a ingestão SP2 de forma idempotente, espelhando workers/document_processing da
referência. Opera no cursor de uma transação tenant_tx (RLS por org).

Idempotência em duas camadas:
  - por job (organization_id, job_id) via agent_executions: completed -> não refaz;
    running/retrying -> em andamento; failed -> retry só dentro do limite de tentativas;
  - por documento: se já existem chunks, pula ('document_already_processed').
"""
from __future__ import annotations

import logging

from src.schemas.queue_schemas import DocumentProcessingJob, WorkerResult
from src.services import agent_executions as ae
from src.services.document_ingestion import ingest_document as _ingest_document

logger = logging.getLogger()

DEFAULT_MAX_ATTEMPTS = 3


def _result(job, status, reason=None) -> WorkerResult:
    return WorkerResult(job_id=job.job_id, document_id=job.document_id,
                        status=status, reason=reason)


def process_job(cur, org, job: DocumentProcessingJob, *,
                max_attempts: int = DEFAULT_MAX_ATTEMPTS, ingest=_ingest_document) -> WorkerResult:
    """Processa um job idempotentemente. Não faz commit/rollback (responsabilidade do chamador)."""
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

    try:
        cur.execute("SELECT id, case_id FROM public.documents WHERE id = %s", (str(job.document_id),))
        doc = cur.fetchone()
        if doc is None:
            raise LookupError("document_not_found")
        cur.execute("SELECT 1 FROM public.cases WHERE id = %s", (str(job.case_id),))
        if cur.fetchone() is None:
            raise LookupError("case_not_found")
        if str(doc["case_id"]) != str(job.case_id):
            raise LookupError("document_not_for_case")

        cur.execute("SELECT 1 FROM public.document_chunks WHERE document_id = %s LIMIT 1",
                    (str(job.document_id),))
        if cur.fetchone() is not None:
            ae.mark_skipped(cur, execution["id"], "document_already_processed")
            return _result(job, "skipped", "document_already_processed")

        execution = ae.mark_running(cur, execution["id"], job.attempt)
        result = ingest(cur, org, job.document_id)
        ae.mark_completed(cur, execution["id"], {
            "status": result.get("status"), "chunk_count": result.get("chunk_count"),
            "embedding_count": result.get("embedding_count")})
        return _result(job, "completed")
    except Exception as exc:  # noqa: BLE001 — mapeia qualquer falha para agent_execution.failed
        ae.mark_failed(cur, execution["id"], exc.__class__.__name__)
        logger.info('{"event":"DOC_JOB_FAILED","error":"%s"}' % exc.__class__.__name__)
        return _result(job, "failed", exc.__class__.__name__)
