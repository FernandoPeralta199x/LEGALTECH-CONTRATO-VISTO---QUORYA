"""Fundação V2 — RLS da tabela organizations (PG18 + role não-owner cv_app)."""
import uuid

import psycopg2
import pytest

from src.services.database import get_connection

SYSTEM_ORG_ID = "00000000-0000-0000-0000-000000000001"


def _admin_conn():
    return psycopg2.connect(
        host="localhost", port=5433, user="dbadmin",
        password="localdev_cv", dbname="contrato_visto", connect_timeout=5,
    )


def _set_org(cur, org_id):
    cur.execute("SELECT set_config('app.organization_id', %s, true)", (str(org_id),))


def test_org_visible_only_within_its_context():
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    admin = _admin_conn()
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute("SELECT set_config('app.organization_id', %s, false)", (org_a,))
        c.execute("INSERT INTO public.organizations (id, name) VALUES (%s, 'A')", (org_a,))
        c.execute("SELECT set_config('app.organization_id', %s, false)", (org_b,))
        c.execute("INSERT INTO public.organizations (id, name) VALUES (%s, 'B')", (org_b,))
    admin.close()

    conn = get_connection()
    cur = conn.cursor()
    try:
        _set_org(cur, org_a)
        cur.execute("SELECT count(*) FROM public.organizations")
        assert cur.fetchone()[0] == 1  # só a própria org
        cur.execute("SELECT id FROM public.organizations")
        assert str(cur.fetchone()[0]) == org_a
        conn.rollback()
    finally:
        cur.close()


def test_org_without_context_is_blocked():
    conn = get_connection()
    cur = conn.cursor()
    try:
        with pytest.raises(psycopg2.Error):
            cur.execute("SELECT count(*) FROM public.organizations")
        conn.rollback()
    finally:
        cur.close()
