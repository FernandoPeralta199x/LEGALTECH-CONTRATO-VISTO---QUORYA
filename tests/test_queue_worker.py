"""SP1 — testes da fila assíncrona (PG18 + RLS): enfileiramento inline, idempotência
por job (agent_executions, UNIQUE org+job_id), dedupe por documento (chunks existentes),
falhas determinísticas, rejeição de metadata sensível e o handler SQS (partial batch).
"""
from _dbadmin import admin_conn
import json
import uuid

import psycopg2
import pytest
from pydantic import ValidationError

from src.handlers import documents as doc_h
from src.handlers import requests as req_h
from src.handlers import worker as worker_h
from src.schemas.queue_schemas import DocumentProcessingJob
from src.services import agent_executions as ae
from src.services import document_worker
from src.services.database import tenant_tx
from src.services.document_ingestion import OcrFailed

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"


def _admin_conn():
    return admin_conn()


def _reset():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.agent_executions, public.document_embeddings,"
            " public.document_chunks, public.timeline_events, public.triage_modules,"
            " public.case_parties, public.documents, public.requests, public.cases,"
            " public.clients, public.request_code_sequences, public.pricing_configs"
            " RESTART IDENTITY CASCADE")
    conn.close()


def _seed_writer(user_id, role, org=SYSTEM_ORG):
    """Semeia o user do token no banco com o papel dado. As rotas de escrita reconsultam
    o papel ATUAL (assert_active_writer, SEC-01), então um user_id não-semeado seria 403.
    ON CONFLICT DO NOTHING: não pisa num user já semeado com o mesmo id."""
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s,%s,'x','Test',%s,'active',%s) ON CONFLICT (id) DO NOTHING",
            (user_id, f"u_{user_id}@t.c", role, org))
    conn.close()


def _event(user_id, role="admin", path=None, org_id=SYSTEM_ORG, body=None):
    # O user do token existe no banco com o papel do teste (senão o recheck de escrita
    # do SEC-01 recusaria antes de exercitar o comportamento sob teste).
    _seed_writer(user_id, role, org_id)
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "perfil": "administrador", "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": {},
    }


def _data(resp, *codes):
    assert resp["statusCode"] in (codes or (200, 201, 202)), resp
    return json.loads(resp["body"])["data"]


def _wizard_payload():
    return {
        "product_type": "analise_contratual",
        "title": "Contrato p/ fila",
        "parties": [{"name": "Empresa Z LTDA", "role": "contratante",
                     "person_type": "company", "document": "11222333000181"}],
        "document": {"filename": "contrato.pdf", "size_bytes": 2048},
        "selected_modules": ["ia_deepseek"],
    }


def _doc_id_do_caso(case_id):
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM public.documents WHERE case_id = %s", (case_id,))
        row = cur.fetchone()
    conn.close()
    return str(row[0])


def _count(sql, params):
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.fetchone()[0]
    conn.close()
    return n


def _rows(sql, params):
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    conn.close()
    return rows


@pytest.fixture()
def ctx():
    _reset()
    admin = str(uuid.uuid4())
    data = _data(req_h.create_request(_event(admin, body=_wizard_payload()), None))
    case_id = data["case_id"]
    return {"admin": admin, "case_id": case_id, "doc_id": _doc_id_do_caso(case_id)}


# ── enfileiramento inline (dev) ──────────────────────────────────────────────
def test_enqueue_inline_processa_documento(ctx):
    resp = doc_h.enqueue_processing(_event(ctx["admin"], path={"docId": ctx["doc_id"]}), None)
    assert resp["statusCode"] == 202
    data = _data(resp, 202)
    assert data["status"] == "queued"
    assert data["queue_backend"] == "inline"
    assert data["document_id"] == ctx["doc_id"]
    assert data["job_id"]
    # execução registrada como completed + chunks gerados + documento processado
    assert _count("SELECT count(*) FROM public.agent_executions WHERE case_id=%s AND status='completed'",
                  (ctx["case_id"],)) == 1
    assert _count("SELECT count(*) FROM public.document_chunks WHERE document_id=%s",
                  (ctx["doc_id"],)) > 0
    assert _count("SELECT count(*) FROM public.documents WHERE id=%s AND ocr_status='done'",
                  (ctx["doc_id"],)) == 1


def test_enqueue_documento_inexistente_404(ctx):
    resp = doc_h.enqueue_processing(_event(ctx["admin"], path={"docId": str(uuid.uuid4())}), None)
    assert resp["statusCode"] == 404


def test_enqueue_viewer_403(ctx):
    resp = doc_h.enqueue_processing(
        _event(ctx["admin"], role="viewer", path={"docId": ctx["doc_id"]}), None)
    assert resp["statusCode"] == 403


def test_segundo_enqueue_pula_por_documento_ja_processado(ctx):
    doc_h.enqueue_processing(_event(ctx["admin"], path={"docId": ctx["doc_id"]}), None)
    chunks1 = _count("SELECT count(*) FROM public.document_chunks WHERE document_id=%s", (ctx["doc_id"],))
    # segundo enqueue: novo job, mas o documento já tem chunks -> skipped
    doc_h.enqueue_processing(_event(ctx["admin"], path={"docId": ctx["doc_id"]}), None)
    chunks2 = _count("SELECT count(*) FROM public.document_chunks WHERE document_id=%s", (ctx["doc_id"],))
    assert chunks2 == chunks1  # não reprocessou
    assert _count("SELECT count(*) FROM public.agent_executions WHERE case_id=%s AND status='skipped'",
                  (ctx["case_id"],)) == 1


# ── idempotência por job (process_job direto) ────────────────────────────────
def _job(ctx, **over):
    base = dict(organization_id=SYSTEM_ORG, case_id=ctx["case_id"],
                document_id=ctx["doc_id"], requested_by=ctx["admin"], metadata={"source": "test"})
    base.update(over)
    return DocumentProcessingJob(**base)


def test_idempotencia_duplicate_completed(ctx):
    job = _job(ctx)
    with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
        r1 = document_worker.process_job(cur, SYSTEM_ORG, job)
    assert r1.status == "completed" and r1.reason is None
    # mesmo job_id de novo -> duplicate_completed
    with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
        r2 = document_worker.process_job(cur, SYSTEM_ORG, job)
    assert r2.status == "completed" and r2.reason == "duplicate_completed"
    assert _count("SELECT count(*) FROM public.agent_executions WHERE job_id=%s",
                  (str(job.job_id),)) == 1


def test_documento_ja_processado_outro_job(ctx):
    with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
        document_worker.process_job(cur, SYSTEM_ORG, _job(ctx))
    # novo job, mesmo documento (já tem chunks) -> document_already_processed
    with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
        r = document_worker.process_job(cur, SYSTEM_ORG, _job(ctx))
    assert r.status == "skipped" and r.reason == "document_already_processed"


def test_job_falha_quando_documento_inexistente(ctx):
    job = _job(ctx, document_id=str(uuid.uuid4()))
    with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
        r = document_worker.process_job(cur, SYSTEM_ORG, job)
    assert r.status == "failed"
    assert _count("SELECT count(*) FROM public.agent_executions WHERE job_id=%s AND status='failed'",
                  (str(job.job_id),)) == 1


def test_falha_deterministica_ocr_marca_failed_e_ack(ctx):
    def _ocr_fail(cur, org, doc_id):
        raise OcrFailed(str(doc_id))
    job = _job(ctx)
    with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
        r = document_worker.process_job(cur, SYSTEM_ORG, job, ingest=_ocr_fail)
    assert r.status == "failed" and r.reason == "OcrFailed"
    # determinística: o registro 'failed' persiste (ack, sem retry)
    assert _count("SELECT count(*) FROM public.agent_executions WHERE job_id=%s AND status='failed'",
                  (str(job.job_id),)) == 1


def test_falha_transitoria_propaga_e_faz_rollback(ctx):
    def _boom(cur, org, doc_id):
        raise RuntimeError("infra_down")  # transitória: deve PROPAGAR
    job = _job(ctx)
    with pytest.raises(RuntimeError):
        with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
            document_worker.process_job(cur, SYSTEM_ORG, job, ingest=_boom)
    # rollback total: nem execução nem chunks persistem
    assert _count("SELECT count(*) FROM public.agent_executions WHERE job_id=%s",
                  (str(job.job_id),)) == 0
    assert _count("SELECT count(*) FROM public.document_chunks WHERE document_id=%s",
                  (ctx["doc_id"],)) == 0


def test_falha_transitoria_nao_deixa_chunks_parciais(ctx):
    def _parcial(cur, org, doc_id):
        # grava 1 chunk e então falha (simula embed cair no meio do loop)
        cur.execute("INSERT INTO public.document_chunks"
                    " (organization_id, document_id, chunk_index, chunk_text)"
                    " VALUES (%s,%s,0,'parcial')", (SYSTEM_ORG, str(doc_id)))
        raise RuntimeError("embed_down")
    job = _job(ctx)
    with pytest.raises(RuntimeError):
        with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
            document_worker.process_job(cur, SYSTEM_ORG, job, ingest=_parcial)
    assert _count("SELECT count(*) FROM public.document_chunks WHERE document_id=%s",
                  (ctx["doc_id"],)) == 0


def test_job_already_running_skip(ctx):
    job = _job(ctx)
    # cria a execução e a deixa 'running' (em andamento)
    with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
        ex = ae.create_queued(cur, SYSTEM_ORG, job)
        ae.mark_running(cur, ex["id"], job.attempt)
    # mesmo job_id chega de novo -> skipped (job_already_running)
    with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
        r = document_worker.process_job(cur, SYSTEM_ORG, job)
    assert r.status == "skipped" and r.reason == "job_already_running"


def test_timeline_de_auditoria_registrada(ctx):
    with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
        document_worker.process_job(cur, SYSTEM_ORG, _job(ctx))
    tipos = {r[0] for r in _rows(
        "SELECT event_type FROM public.timeline_events WHERE case_id=%s", (ctx["case_id"],))}
    assert {"document_processing_started", "document_processed"} <= tipos


def test_max_attempts_excedido(ctx):
    job = _job(ctx, attempt=4)  # > max_attempts (3)
    with tenant_tx(ctx["admin"], "admin", SYSTEM_ORG) as cur:
        r = document_worker.process_job(cur, SYSTEM_ORG, job, max_attempts=3)
    assert r.status == "failed" and r.reason == "MaxAttemptsExceeded"


# ── segurança do contrato ────────────────────────────────────────────────────
def test_metadata_sensivel_rejeitada(ctx):
    with pytest.raises(ValidationError):
        _job(ctx, metadata={"text": "conteúdo do contrato vazado"})


# ── handler SQS (partial batch response) ─────────────────────────────────────
def test_worker_sqs_processa_record_valido(ctx):
    job = _job(ctx)
    event = {"Records": [{"messageId": "m1", "body": job.model_dump_json()}]}
    out = worker_h.document_processing_worker(event, None)
    assert out["batchItemFailures"] == []
    assert _count("SELECT count(*) FROM public.document_chunks WHERE document_id=%s",
                  (ctx["doc_id"],)) > 0


def test_worker_sqs_body_invalido_vira_batch_failure(ctx):
    event = {"Records": [{"messageId": "bad", "body": "{nao-e-json}"}]}
    out = worker_h.document_processing_worker(event, None)
    assert out["batchItemFailures"] == [{"itemIdentifier": "bad"}]
