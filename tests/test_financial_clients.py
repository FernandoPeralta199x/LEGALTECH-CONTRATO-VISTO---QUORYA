"""Testes da aba Clientes — receita por cliente via requests→cases→clients.

Cobre: vazio coerente, agregação por cliente, bucket honesto 'Sem cliente
atribuído' (vendas sem caso/cliente reconciliam com o bruto global), isolamento
por org (RLS) e janela de período.
"""
from _dbadmin import admin_conn
import json
import uuid

import pytest

from src.handlers import financial_clients as cli_h

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


def _seed_client(org, name):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.clients (organization_id, legal_name, document_number)"
                    " VALUES (%s,%s,%s) RETURNING id", (org, name, uuid.uuid4().hex[:18]))
        cid = cur.fetchone()[0]
    conn.close()
    return cid


def _seed_case(org, client_id):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.cases (organization_id, client_id, case_type)"
                    " VALUES (%s,%s,'fin') RETURNING id", (org, client_id))
        caseid = cur.fetchone()[0]
    conn.close()
    return caseid


def _seed_request(org=SYSTEM_ORG, price=10000, pay="paid", case_id=None, created_at=None):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.requests"
            " (organization_id, created_by, code, product_type, product_label, title,"
            "  status, source_mode, total_price_cents, payment_status, case_id, created_at)"
            " VALUES (%s, gen_random_uuid(), %s, 'analise_contratual', 'Análise', 'T',"
            "         'created', 'manual', %s, %s, %s, COALESCE(%s, now()))",
            (org, "R-" + uuid.uuid4().hex[:10], price, pay, case_id, created_at))
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


def test_clients_vazio():
    a = _admin()
    d = _data(cli_h.list_clients_revenue(_event(a), None))
    assert d["summary"]["clients_count"] == 0 and d["summary"]["gross_cents"] == 0
    assert d["items"] == [] and d["total"] == 0


def test_clients_agrega_e_bucket_sem_cliente():
    a = _admin()
    cid = _seed_client(SYSTEM_ORG, "Cliente A")
    case = _seed_case(SYSTEM_ORG, cid)
    _seed_request(price=30000, pay="paid", case_id=case)      # atribuído ao Cliente A
    _seed_request(price=8000, pay="pending", case_id=None)    # sem caso → 'Sem cliente'

    d = _data(cli_h.list_clients_revenue(_event(a), None))
    s = d["summary"]
    assert s["clients_count"] == 1 and s["unassigned_count"] == 1
    assert s["gross_cents"] == 38000 and s["unassigned_gross_cents"] == 8000
    # reconciliação: soma dos itens == bruto global
    assert sum(i["gross_cents"] for i in d["items"]) == 38000
    # ordenado por bruto desc: Cliente A (30000) antes do bucket (8000)
    assert d["items"][0]["client_name"] == "Cliente A" and d["items"][0]["gross_cents"] == 30000
    assert d["items"][0]["received_cents"] == 30000 and d["items"][0]["client_id"] is not None
    # bucket honesto: client_id/client_name None
    assert d["items"][1]["client_id"] is None and d["items"][1]["client_name"] is None
    assert d["items"][1]["gross_cents"] == 8000 and d["items"][1]["pending_cents"] == 8000
    assert d["total"] == 2


def test_clients_isolado_por_org():
    a = _admin()
    cid = _seed_client(SYSTEM_ORG, "Cliente A")
    case = _seed_case(SYSTEM_ORG, cid)
    _seed_request(price=30000, case_id=case)
    b = _admin(org=OTHER_ORG)  # admin ATIVO na outra org: o 0 vem da RLS, nao do 403 (SEC-02b)
    d = _data(cli_h.list_clients_revenue(_event(b, org_id=OTHER_ORG), None))
    assert d["summary"]["gross_cents"] == 0 and d["items"] == []


def test_clients_periodo():
    a = _admin()
    _seed_request(price=10000, case_id=None)  # now → today/month
    assert _data(cli_h.list_clients_revenue(_event(a, query={"period": "lastMonth"}), None))["summary"]["count"] == 0
    assert _data(cli_h.list_clients_revenue(_event(a, query={"period": "today"}), None))["summary"]["count"] == 1


def test_clients_paginacao_estavel_com_empate():
    # 3 clientes + bucket 'Sem cliente' TODOS empatando em gross → sem chave única
    # de desempate o OFFSET duplicaria/pularia. Percorre page_size=1 e exige que os
    # 4 grupos apareçam exatamente uma vez (paginação determinística).
    a = _admin()
    for name in ("Cli A", "Cli B", "Cli C"):
        cid = _seed_client(SYSTEM_ORG, name)
        case = _seed_case(SYSTEM_ORG, cid)
        _seed_request(price=10000, pay="paid", case_id=case)
    _seed_request(price=10000, case_id=None)  # bucket 'Sem cliente' — mesmo gross

    seen = []
    total = None
    for page in range(1, 7):
        d = _data(cli_h.list_clients_revenue(_event(a, query={"page": str(page), "page_size": "1"}), None))
        total = d["total"]
        if not d["items"]:
            break
        seen.append(d["items"][0]["client_id"])

    assert total == 4 and len(seen) == 4          # 4 grupos, um por página
    assert seen.count(None) == 1                    # bucket exatamente uma vez
    assert len({x for x in seen if x is not None}) == 3  # 3 clientes distintos
