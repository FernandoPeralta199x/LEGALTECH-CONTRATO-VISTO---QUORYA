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
            "TRUNCATE public.case_parties, public.requests,"
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
