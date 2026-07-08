"""Coração 5 — testes do dashboard (stats agregados por organização, PG18 + RLS)."""
from _dbadmin import admin_conn
import json
import uuid

import psycopg2
import pytest

from src.handlers import dashboard as dash_h
from src.handlers import requests as req_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _admin_conn():
    return admin_conn()


def _reset():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.timeline_events, public.triage_modules, public.case_parties,"
            " public.documents, public.requests, public.cases, public.clients,"
            " public.request_code_sequences, public.pricing_configs RESTART IDENTITY CASCADE")
    conn.close()


def _event(user_id, role="admin", org_id=SYSTEM_ORG, body=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {},
        "queryStringParameters": {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


def _payload(product_type="analise_contratual"):
    return {
        "product_type": product_type,
        "title": "Caso dashboard",
        "parties": [{"name": "Parte A", "role": "contratante"}],
        "document": {"filename": "doc.pdf", "size_bytes": 100},
        "selected_modules": ["ia_deepseek"],
    }


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_dashboard_vazio():
    a = str(uuid.uuid4())
    stats = _data(dash_h.get_stats(_event(a), None))
    assert stats["total_cases"] == 0
    assert stats["total_requests"] == 0
    assert stats["recent_cases"] == []


def test_dashboard_conta_casos_e_pedidos():
    a = str(uuid.uuid4())
    req_h.create_request(_event(a, body=_payload()), None)
    req_h.create_request(_event(a, body=_payload("dados_partes")), None)
    stats = _data(dash_h.get_stats(_event(a), None))
    assert stats["total_cases"] == 2
    assert stats["total_requests"] == 2
    assert stats["cases_by_status"] == {"awaiting_triage": 2}
    assert len(stats["recent_cases"]) == 2
    assert stats["recent_cases"][0]["code"].startswith("REQ-")


def test_dashboard_isolado_por_org():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    req_h.create_request(_event(a, body=_payload()), None)
    # outra organização não enxerga os casos/pedidos (RLS)
    stats_other = _data(dash_h.get_stats(_event(b, org_id=OTHER_ORG), None))
    assert stats_other["total_cases"] == 0
    assert stats_other["total_requests"] == 0
