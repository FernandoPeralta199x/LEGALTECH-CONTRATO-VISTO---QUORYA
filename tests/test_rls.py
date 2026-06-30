"""Fase 1.6 — Fundação RLS validada de verdade (PG18 + role não-owner cv_app).

Cobre: isolamento por usuário, trigger de auditoria com app.user_id, reset de
contexto entre usuários na mesma conexão reutilizada, e fail-closed sem contexto.
"""
import uuid

import psycopg2
import pytest

from src.services.database import get_connection, tenant_tx

# Org de sistema (migração 005) — a RLS de cases/clients ainda é por-dono nesta
# fase; o organization_id é exigido pela assinatura de tenant_tx mas é inócuo aqui.
SYSTEM_ORG_ID = "00000000-0000-0000-0000-000000000001"


def _new_user() -> str:
    return str(uuid.uuid4())


def _admin_conn():
    """Conexão como owner (dbadmin) — bypassa RLS — só para setup/asserts."""
    return psycopg2.connect(
        host="localhost", port=5433, user="dbadmin",
        password="localdev_cv", dbname="contrato_visto", connect_timeout=5,
    )


def _reset():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.cases, public.clients RESTART IDENTITY CASCADE"
        )
        cur.execute("TRUNCATE audit.audit_log RESTART IDENTITY")
    conn.close()


def _seed_case(user_id: str) -> str:
    with tenant_tx(user_id, "analyst", SYSTEM_ORG_ID) as cur:
        cur.execute(
            "INSERT INTO public.clients (legal_name, document_number, document_type)"
            " VALUES ('Cliente Teste', %s, 'cnpj') RETURNING id",
            (uuid.uuid4().hex[:14],),
        )
        client_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO public.cases (client_id, case_type, created_by)"
            " VALUES (%s, 'contract_analysis', %s) RETURNING id",
            (client_id, user_id),
        )
        return cur.fetchone()["id"]


@pytest.fixture(autouse=True)
def clean_db():
    _reset()
    yield
    _reset()


def test_owner_sees_case_other_user_does_not():
    user_a, user_b = _new_user(), _new_user()
    _seed_case(user_a)

    with tenant_tx(user_a, "analyst", SYSTEM_ORG_ID) as cur:
        cur.execute("SELECT count(*) AS n FROM public.cases")
        assert cur.fetchone()["n"] == 1

    with tenant_tx(user_b, "analyst", SYSTEM_ORG_ID) as cur:
        cur.execute("SELECT count(*) AS n FROM public.cases")
        assert cur.fetchone()["n"] == 0


def test_admin_sees_all_cases():
    user_a, admin = _new_user(), _new_user()
    _seed_case(user_a)

    with tenant_tx(admin, "admin", SYSTEM_ORG_ID) as cur:
        cur.execute("SELECT count(*) AS n FROM public.cases")
        assert cur.fetchone()["n"] == 1


def test_update_fires_audit_trigger_with_app_user_id():
    user_a = _new_user()
    case_id = _seed_case(user_a)

    with tenant_tx(user_a, "analyst", SYSTEM_ORG_ID) as cur:
        cur.execute(
            "UPDATE public.cases SET status = 'in_progress' WHERE id = %s",
            (case_id,),
        )

    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, action, resource_type FROM audit.audit_log"
            " WHERE resource_id = %s ORDER BY created_at DESC LIMIT 1",
            (case_id,),
        )
        row = cur.fetchone()
    conn.close()

    assert row is not None, "trigger de auditoria não gravou o evento"
    assert str(row[0]) == user_a
    assert row[1] == "UPDATE"
    assert row[2] == "cases"


def test_context_does_not_leak_between_users_on_reused_connection():
    user_a, user_b = _new_user(), _new_user()
    _seed_case(user_a)
    # mesma conexão é reutilizada por get_connection; o SET LOCAL não pode vazar
    with tenant_tx(user_b, "analyst", SYSTEM_ORG_ID) as cur:
        cur.execute("SELECT count(*) AS n FROM public.cases")
        assert cur.fetchone()["n"] == 0


def test_query_without_context_is_blocked():
    """Fail-closed: consultar tabela com RLS sem app.user_id estoura."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        with pytest.raises(psycopg2.Error):
            cur.execute("SELECT count(*) FROM public.cases")
        conn.rollback()
    finally:
        cur.close()
