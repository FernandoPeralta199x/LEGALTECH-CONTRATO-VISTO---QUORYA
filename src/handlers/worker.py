"""Worker Lambda (SP1) disparado pela SQS — processa cada Record (DocumentProcessingJob)
de forma idempotente via document_worker. Usa partial batch response: falhas
determinísticas (documento inexistente, OCR falhou) são tratadas e o ack é dado
(não adianta reprocessar); apenas erros de infra/parse viram batchItemFailures, que
a SQS reentrega até esgotar o maxReceiveCount → DLQ.
"""
import json
import logging
import os

from src.schemas.queue_schemas import DocumentProcessingJob
from src.services.database import tenant_tx
from src.services.document_worker import process_job
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()

_SYSTEM_USER = "00000000-0000-0000-0000-000000000000"


def _max_attempts() -> int:
    try:
        return int(os.environ.get("DOCUMENT_PROCESSING_MAX_ATTEMPTS", "3"))
    except ValueError:
        return 3


def document_processing_worker(event, context):
    """Entrada SQS: event['Records'][*]['body'] é o JSON do DocumentProcessingJob."""
    records = event.get("Records") or []
    failures: list[str] = []
    for rec in records:
        message_id = rec.get("messageId")
        try:
            job = DocumentProcessingJob(**json.loads(rec["body"]))
            actor = str(job.requested_by) if job.requested_by else _SYSTEM_USER
            with tenant_tx(actor, "admin", str(job.organization_id)) as cur:
                result = process_job(cur, job.organization_id, job, max_attempts=_max_attempts())
            logger.info(json.dumps({"event": "DOC_JOB_PROCESSED", "job_id": str(job.job_id),
                                    "status": result.status, "reason": result.reason}))
        except Exception as e:  # erro de infra/parse -> reentrega (DLQ ao esgotar)
            logger.error(json.dumps({"event": "DOC_JOB_INFRA_ERROR", "error": type(e).__name__,
                                     "message_id": message_id}))
            if message_id:
                failures.append(message_id)
    return {"batchItemFailures": [{"itemIdentifier": mid} for mid in failures]}
