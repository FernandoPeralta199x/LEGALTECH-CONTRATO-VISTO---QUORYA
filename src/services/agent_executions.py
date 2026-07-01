"""Execuções de agentes (SP1) — idempotência por (organization_id, job_id) e transições
de estado, espelhando modules/agent_executions da referência. Opera no cursor de uma
transação tenant_tx (RLS por org). Estados: queued → running → completed | failed | skipped;
retrying ao reprocessar um failed dentro do limite de tentativas.
"""
from __future__ import annotations

from psycopg2.extras import Json

# estados (AgentExecutionStatus da referência)
QUEUED = "queued"
RUNNING = "running"
RETRYING = "retrying"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"

COMPLETED_IDEMPOTENT = frozenset({COMPLETED})
BUSY_IDEMPOTENT = frozenset({RUNNING, RETRYING})

_COLS = ("id, organization_id, case_id, document_id, job_id, agent_type, status,"
         " attempt, input_payload, output_payload, error_message, started_at,"
         " completed_at, created_at, updated_at")


def is_completed(status: str | None) -> bool:
    return status in COMPLETED_IDEMPOTENT


def is_busy(status: str | None) -> bool:
    return status in BUSY_IDEMPOTENT


def can_retry_attempt(*, current_status, current_attempt, requested_attempt, max_attempts) -> bool:
    if current_status != FAILED:
        return False
    return current_attempt < requested_attempt <= max_attempts


def max_attempts_exceeded(*, job_attempt: int, max_attempts: int) -> bool:
    return job_attempt > max_attempts


def get_by_job_id(cur, job_id):
    """Busca a execução pelo job_id (RLS já restringe à org da transação)."""
    cur.execute(f"SELECT {_COLS} FROM public.agent_executions WHERE job_id = %s", (str(job_id),))
    return cur.fetchone()


def create_queued(cur, org, job) -> dict:
    """Cria a execução em 'queued' (idempotente por org+job_id via UNIQUE)."""
    input_payload = {
        "job_type": job.job_type, "agent_type": job.agent_type,
        "case_id": str(job.case_id), "document_id": str(job.document_id),
        "source": job.metadata.get("source", "queue"),
    }
    cur.execute(
        "INSERT INTO public.agent_executions"
        " (organization_id, case_id, document_id, job_id, agent_type, status, attempt, input_payload)"
        f" VALUES (%s,%s,%s,%s,%s,'{QUEUED}',%s,%s) RETURNING {_COLS}",
        (str(org), str(job.case_id), str(job.document_id), str(job.job_id),
         job.agent_type, job.attempt, Json(input_payload)))
    return cur.fetchone()


def _update(cur, exec_id, sets: str, params: tuple) -> dict:
    cur.execute(
        f"UPDATE public.agent_executions SET {sets}, updated_at = now()"
        f" WHERE id = %s RETURNING {_COLS}", (*params, str(exec_id)))
    return cur.fetchone()


def mark_retrying(cur, exec_id, attempt) -> dict:
    return _update(cur, exec_id, f"status='{RETRYING}', attempt=%s, error_message=NULL", (attempt,))


def mark_running(cur, exec_id, attempt) -> dict:
    return _update(cur, exec_id,
                   f"status='{RUNNING}', attempt=%s, error_message=NULL,"
                   " started_at=now(), completed_at=NULL", (attempt,))


def mark_completed(cur, exec_id, output: dict) -> dict:
    return _update(cur, exec_id,
                   f"status='{COMPLETED}', output_payload=%s, error_message=NULL,"
                   " completed_at=now()", (Json(output),))


def mark_failed(cur, exec_id, error_message: str) -> dict:
    return _update(cur, exec_id,
                   f"status='{FAILED}', output_payload='{{}}'::jsonb, error_message=%s,"
                   " completed_at=now()", ((error_message or "")[:500],))


def mark_skipped(cur, exec_id, reason: str) -> dict:
    return _update(cur, exec_id,
                   f"status='{SKIPPED}', output_payload=%s, error_message=NULL,"
                   " completed_at=now()", (Json({"reason": reason}),))
