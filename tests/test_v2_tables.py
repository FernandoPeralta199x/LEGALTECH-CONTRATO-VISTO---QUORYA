"""Fundação V2 — RLS por organização das tabelas estruturais novas (PG18 + cv_app)."""
import uuid

import psycopg2
import pytest

from src.services.database import get_connection, tenant_tx

ORG_A = "00000000-0000-0000-0000-000000000001"  # org de sistema (migração 005)
ORG_B = "00000000-0000-0000-0000-0000000000ff"  # outra org (só leitura nos testes)


def _uid() -> str:
    return str(uuid.uuid4())


def _admin_conn():
    return psycopg2.connect(
        host="localhost", port=5433, user="dbadmin",
        password="localdev_cv", dbname="contrato_visto", connect_timeout=5,
    )


@pytest.fixture(autouse=True)
def clean():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.timeline_events, public.provider_results, public.triage_modules,"
            " public.agent_executions, public.external_queries_cache,"
            " public.pricing_configs, public.case_parties, public.requests,"
            " public.request_code_sequences, public.cases, public.clients"
            " RESTART IDENTITY CASCADE")
    conn.close()
    yield


def test_requests_isolated_by_org():
    with tenant_tx(_uid(), "admin", ORG_A) as cur:
        cur.execute(
            "INSERT INTO public.requests"
            " (organization_id, created_by, code, product_type, product_label,"
            "  title, status, source_mode)"
            " VALUES (%s, %s, 'REQ-1', 'dados_partes', 'Dados das partes',"
            "         'Pedido teste', 'draft', 'local')",
            (ORG_A, _uid()),
        )
    # mesma org vê
    with tenant_tx(_uid(), "admin", ORG_A) as cur:
        cur.execute("SELECT count(*) AS n FROM public.requests")
        assert cur.fetchone()["n"] == 1
    # outra org não vê
    with tenant_tx(_uid(), "admin", ORG_B) as cur:
        cur.execute("SELECT count(*) AS n FROM public.requests")
        assert cur.fetchone()["n"] == 0


def test_case_parties_isolated_by_org():
    with tenant_tx(_uid(), "admin", ORG_A) as cur:
        cur.execute(
            "INSERT INTO public.cases (organization_id, case_type, created_by)"
            " VALUES (%s, 'contract_analysis', %s) RETURNING id",
            (ORG_A, _uid()),
        )
        case_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO public.case_parties (organization_id, case_id, party_type, name)"
            " VALUES (%s, %s, 'contratante', 'Fulano de Tal')",
            (ORG_A, case_id),
        )
    with tenant_tx(_uid(), "admin", ORG_A) as cur:
        cur.execute("SELECT count(*) AS n FROM public.case_parties")
        assert cur.fetchone()["n"] == 1
    with tenant_tx(_uid(), "admin", ORG_B) as cur:
        cur.execute("SELECT count(*) AS n FROM public.case_parties")
        assert cur.fetchone()["n"] == 0


def test_requests_without_context_is_blocked():
    conn = get_connection()
    cur = conn.cursor()
    try:
        with pytest.raises(psycopg2.Error):
            cur.execute("SELECT count(*) FROM public.requests")
        conn.rollback()
    finally:
        cur.close()


def test_pipeline_tables_isolated_by_org():
    with tenant_tx(_uid(), "admin", ORG_A) as cur:
        cur.execute(
            "INSERT INTO public.cases (organization_id, case_type, created_by)"
            " VALUES (%s, 'contract_analysis', %s) RETURNING id", (ORG_A, _uid()))
        case_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO public.triage_modules"
            " (organization_id, case_id, module_key, module_label, provider, status, source_mode)"
            " VALUES (%s, %s, 'escavador', 'Escavador', 'escavador', 'pending', 'mock')",
            (ORG_A, case_id))
        cur.execute(
            "INSERT INTO public.agent_executions (organization_id, case_id, job_id, agent_type)"
            " VALUES (%s, %s, %s, 'triagem')", (ORG_A, case_id, _uid()))
        cur.execute(
            "INSERT INTO public.external_queries_cache"
            " (organization_id, provider, query_hash, request_payload)"
            " VALUES (%s, 'escavador', 'h1', '{}'::jsonb)", (ORG_A,))
        cur.execute(
            "INSERT INTO public.pricing_configs (organization_id, cases_limit)"
            " VALUES (%s, 100)", (ORG_A,))
    # outra organização não vê nada do pipeline
    with tenant_tx(_uid(), "admin", ORG_B) as cur:
        for t in ("triage_modules", "agent_executions",
                  "external_queries_cache", "pricing_configs"):
            cur.execute(f"SELECT count(*) AS n FROM public.{t}")  # nome interno fixo
            assert cur.fetchone()["n"] == 0
    # mesma organização vê
    with tenant_tx(_uid(), "admin", ORG_A) as cur:
        cur.execute("SELECT count(*) AS n FROM public.agent_executions")
        assert cur.fetchone()["n"] == 1


def test_agent_executions_job_id_unique_per_org():
    job = _uid()
    with tenant_tx(_uid(), "admin", ORG_A) as cur:
        cur.execute(
            "INSERT INTO public.cases (organization_id, case_type, created_by)"
            " VALUES (%s, 'contract_analysis', %s) RETURNING id", (ORG_A, _uid()))
        case_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO public.agent_executions (organization_id, case_id, job_id, agent_type)"
            " VALUES (%s, %s, %s, 'triagem')", (ORG_A, case_id, job))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        with tenant_tx(_uid(), "admin", ORG_A) as cur:
            cur.execute(
                "INSERT INTO public.cases (organization_id, case_type, created_by)"
                " VALUES (%s, 'contract_analysis', %s) RETURNING id", (ORG_A, _uid()))
            case_id2 = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO public.agent_executions (organization_id, case_id, job_id, agent_type)"
                " VALUES (%s, %s, %s, 'triagem')", (ORG_A, case_id2, job))  # job repetido


def test_timeline_events_isolated_by_org():
    with tenant_tx(_uid(), "admin", ORG_A) as cur:
        cur.execute(
            "INSERT INTO public.cases (organization_id, case_type, created_by)"
            " VALUES (%s, 'contract_analysis', %s) RETURNING id", (ORG_A, _uid()))
        case_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO public.timeline_events"
            " (organization_id, case_id, event_type, title, actor)"
            " VALUES (%s, %s, 'request_created', 'Pedido criado', 'system')",
            (ORG_A, case_id))
    with tenant_tx(_uid(), "admin", ORG_A) as cur:
        cur.execute("SELECT count(*) AS n FROM public.timeline_events")
        assert cur.fetchone()["n"] == 1
    with tenant_tx(_uid(), "admin", ORG_B) as cur:
        cur.execute("SELECT count(*) AS n FROM public.timeline_events")
        assert cur.fetchone()["n"] == 0
