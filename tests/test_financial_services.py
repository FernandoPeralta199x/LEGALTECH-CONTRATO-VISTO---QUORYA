"""Testes da aba Serviços — agregação read-only de requests por product_type.

Cobre: vazio coerente, agregação por serviço (bruto/recebido/pendente/ticket),
ordenação por bruto, isolamento por org (RLS) e janela de período.
"""
from _dbadmin import admin_conn
import json
import uuid

import pytest

from src.handlers import financial_services as svc_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _reset():
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.requests, public.cases, public.clients"
                    " RESTART IDENTITY CASCADE")
    conn.close()


def _seed_user(user_id, org=SYSTEM_ORG, role="admin", status="active"):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s,%s,'x','Test',%s,%s,%s)"
            " ON CONFLICT (id) DO UPDATE SET role=EXCLUDED.role, status=EXCLUDED.status",
            (user_id, f"u_{user_id}@t.c", role, status, org))
    conn.close()


def _admin(org=SYSTEM_ORG):
    uid = str(uuid.uuid4())
    _seed_user(uid, org=org)
    return uid


def _seed_request(org=SYSTEM_ORG, product_type="analise_contratual", label="Análise",
                  price=10000, pay="paid", case_id=None, created_at=None):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.requests"
            " (organization_id, created_by, code, product_type, product_label, title,"
            "  status, source_mode, total_price_cents, payment_status, case_id, created_at)"
            " VALUES (%s, gen_random_uuid(), %s, %s, %s, 'T', 'created', 'manual', %s, %s, %s,"
            "         COALESCE(%s, now()))",
            (org, "R-" + uuid.uuid4().hex[:10], product_type, label, price, pay, case_id, created_at))
    conn.close()


def _event(user_id, role="admin", org_id=SYSTEM_ORG, query=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "organization_id": org_id}},
        "body": None,
        "pathParameters": {},
        "queryStringParameters": query or {},
    }


def _data(resp, code=200):
    assert resp["statusCode"] == code, resp
    return json.loads(resp["body"]).get("data")


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_services_vazio():
    a = _admin()
    d = _data(svc_h.list_services(_event(a), None))
    assert d["summary"]["services_count"] == 0 and d["summary"]["gross_cents"] == 0
    assert d["summary"]["ticket_cents"] is None
    assert d["summary"]["by_product"] == []
    assert d["currency"] == "BRL" and d["period"] == "month"


def test_services_agrega_por_produto():
    a = _admin()
    _seed_request(product_type="analise_contratual", price=10000, pay="paid")
    _seed_request(product_type="analise_contratual", price=5000, pay="pending")
    _seed_request(product_type="parecer", label="Parecer", price=20000, pay="paid")

    d = _data(svc_h.list_services(_event(a), None))
    s = d["summary"]
    assert s["services_count"] == 2 and s["count"] == 3
    assert s["gross_cents"] == 35000 and s["received_cents"] == 30000 and s["pending_cents"] == 5000
    bp = s["by_product"]
    # ordenado por bruto desc: parecer (20000) antes de analise (15000)
    assert bp[0]["product_type"] == "parecer" and bp[0]["gross_cents"] == 20000
    assert bp[0]["received_cents"] == 20000 and bp[0]["ticket_cents"] == 20000
    assert bp[1]["product_type"] == "analise_contratual" and bp[1]["gross_cents"] == 15000
    assert bp[1]["received_cents"] == 10000 and bp[1]["pending_cents"] == 5000
    assert bp[1]["ticket_cents"] == 7500  # 15000 / 2


def test_services_isolado_por_org():
    a = _admin()
    _seed_request(org=SYSTEM_ORG, price=10000)
    b = _admin(org=OTHER_ORG)  # admin ATIVO na outra org: o 0 vem da RLS, nao do 403 (SEC-02b)
    d = _data(svc_h.list_services(_event(b, org_id=OTHER_ORG), None))
    assert d["summary"]["gross_cents"] == 0 and d["summary"]["by_product"] == []


def test_services_periodo():
    a = _admin()
    _seed_request(price=10000)  # created_at = now() → cai em today/month
    assert _data(svc_h.list_services(_event(a, query={"period": "lastMonth"}), None))["summary"]["count"] == 0
    assert _data(svc_h.list_services(_event(a, query={"period": "today"}), None))["summary"]["count"] == 1


def test_services_periodo_invalido():
    a = _admin()
    assert svc_h.list_services(_event(a, query={"period": "xpto"}), None)["statusCode"] == 400
