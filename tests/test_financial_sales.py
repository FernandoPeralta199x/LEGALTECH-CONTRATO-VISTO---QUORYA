"""Testes da aba Vendas — projeção linha-a-linha sobre requests.

Cobre: vazio coerente, projeção (código/cliente/serviço/valores/status/nota),
cliente não-atribuído, status derivado do pagamento, nota via join LATERAL,
desconto=0 e final=bruto, paginação, busca q, RBAC admin-only + revogação,
isolamento por org (RLS) e RECONCILIAÇÃO com a Visão Geral (mesma fonte).
"""
from _dbadmin import admin_conn
import json
import uuid

import pytest

from src.handlers import financial as ovw_h
from src.handlers import financial_sales as sales_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _reset():
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.requests, public.cases, public.clients,"
                    " public.fiscal_documents RESTART IDENTITY CASCADE")
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
    _seed_user(uid, org=org, role="admin", status="active")
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


def _seed_request(org=SYSTEM_ORG, price=10000, pay="paid", case_id=None, code=None,
                  product_label="Análise", created_at=None):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.requests"
            " (organization_id, created_by, code, product_type, product_label, title,"
            "  status, source_mode, total_price_cents, payment_status, case_id, created_at)"
            " VALUES (%s, gen_random_uuid(), %s, 'analise_contratual', %s, 'T',"
            "         'created', 'manual', %s, %s, %s, COALESCE(%s, now())) RETURNING id",
            (org, code or ("R-" + uuid.uuid4().hex[:10]), product_label, price, pay,
             case_id, created_at))
        rid = cur.fetchone()[0]
    conn.close()
    return rid


def _seed_note(org, request_id, status="authorized"):
    # transação p/ o set_config LOCAL valer: fiscal_documents dispara o trigger de
    # auditoria 'duro' (log_audit), que exige app.user_id.
    conn = admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(uuid.uuid4()),))
        cur.execute("SELECT set_config('app.organization_id', %s, true)", (org,))
        cur.execute(
            "INSERT INTO public.fiscal_documents"
            " (organization_id, doc_type, request_id, status, numero, created_by)"
            " VALUES (%s, 'nfse', %s, %s, %s, gen_random_uuid())",
            (org, request_id, status, uuid.uuid4().hex[:8]))
    conn.commit()
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


def test_sales_vazio():
    a = _admin()
    d = _data(sales_h.list_sales(_event(a), None))
    assert d["items"] == [] and d["total"] == 0
    assert d["summary"]["count"] == 0 and d["summary"]["gross_cents"] == 0
    assert d["summary"]["ticket_cents"] is None


def test_sales_projecao_basica():
    a = _admin()
    cid = _seed_client(SYSTEM_ORG, "Cliente A")
    case = _seed_case(SYSTEM_ORG, cid)
    _seed_request(price=30000, pay="paid", case_id=case, code="R-ABC", product_label="Análise Premium")

    d = _data(sales_h.list_sales(_event(a), None))
    assert d["total"] == 1
    row = d["items"][0]
    assert row["code"] == "R-ABC"
    assert row["client_name"] == "Cliente A"
    assert row["service"] == "Análise Premium"
    assert row["gross_cents"] == 30000
    assert row["discount_cents"] == 0
    assert row["final_cents"] == 30000            # sem desconto → final = bruto
    assert row["sale_status"] == "paid"
    assert row["payment_status"] == "paid"
    assert row["note_status"] == "not_required"   # sem nota vinculada


def test_sales_cliente_nao_atribuido():
    a = _admin()
    _seed_request(price=8000, pay="pending", case_id=None)   # sem caso → sem cliente
    d = _data(sales_h.list_sales(_event(a), None))
    assert d["items"][0]["client_name"] is None              # frontend rotula 'Sem cliente atribuído'
    assert d["items"][0]["client_id"] is None


@pytest.mark.parametrize("pay,expected", [
    ("paid", "paid"),
    ("pending", "pending"),
    ("processing", "pending"),
    ("simulated", "simulated"),
    ("canceled", "canceled"),
    ("expired", "canceled"),
    ("failed", "canceled"),
    ("refunded", "refunded"),
])
def test_sales_status_derivado(pay, expected):
    a = _admin()
    _seed_request(price=5000, pay=pay)
    row = _data(sales_h.list_sales(_event(a), None))["items"][0]
    assert row["payment_status"] == pay
    assert row["sale_status"] == expected


def test_sales_nota_via_join():
    a = _admin()
    rid = _seed_request(price=10000, pay="paid")
    _seed_note(SYSTEM_ORG, rid, status="authorized")
    row = _data(sales_h.list_sales(_event(a), None))["items"][0]
    assert row["note_status"] == "authorized"     # nota reflete a última fiscal_document da venda


def test_sales_nota_pega_a_mais_recente():
    a = _admin()
    rid = _seed_request(price=10000, pay="paid")
    _seed_note(SYSTEM_ORG, rid, status="note_pending")
    _seed_note(SYSTEM_ORG, rid, status="authorized")   # mais recente
    row = _data(sales_h.list_sales(_event(a), None))["items"][0]
    assert row["note_status"] == "authorized"          # LATERAL LIMIT 1 por created_at DESC


def test_sales_paginacao():
    a = _admin()
    for i in range(5):
        _seed_request(price=1000 * (i + 1), pay="paid", code=f"R-{i}")
    d = _data(sales_h.list_sales(_event(a, query={"page": "1", "page_size": "2"}), None))
    assert len(d["items"]) == 2
    assert d["total"] == 5 and d["total_pages"] == 3 and d["page"] == 1
    # summary é do período INTEIRO (não da página): 1000+2000+...+5000
    assert d["summary"]["count"] == 5 and d["summary"]["gross_cents"] == 15000


def test_sales_busca_q():
    a = _admin()
    cid = _seed_client(SYSTEM_ORG, "Acme Corp")
    case = _seed_case(SYSTEM_ORG, cid)
    _seed_request(price=10000, pay="paid", case_id=case, code="R-ACME", product_label="Diligência")
    _seed_request(price=20000, pay="paid", code="R-XYZ", product_label="Análise")

    # por código
    d = _data(sales_h.list_sales(_event(a, query={"q": "ACME"}), None))
    assert d["total"] == 1 and d["items"][0]["code"] == "R-ACME"
    # por nome do cliente
    d = _data(sales_h.list_sales(_event(a, query={"q": "acme corp"}), None))
    assert d["total"] == 1 and d["items"][0]["client_name"] == "Acme Corp"
    # por serviço
    d = _data(sales_h.list_sales(_event(a, query={"q": "Diligência"}), None))
    assert d["total"] == 1 and d["items"][0]["service"] == "Diligência"


def test_sales_busca_filtra_tabela_mas_nao_os_kpis():
    """Honestidade: a busca filtra a LISTA, mas os KPIs continuam do PERÍODO inteiro
    (o card 'Vendas do período' não pode virar subtotal da busca)."""
    a = _admin()
    _seed_request(price=10000, pay="paid", code="R-ACME")
    _seed_request(price=20000, pay="paid", code="R-XYZ")
    d = _data(sales_h.list_sales(_event(a, query={"q": "ACME"}), None))
    assert d["total"] == 1                        # a LISTA é filtrada pela busca
    assert d["summary"]["count"] == 2             # os KPIs são do PERÍODO inteiro
    assert d["summary"]["gross_cents"] == 30000   # bruto do período, não só da busca


def test_sales_admin_only_e_revogacao():
    a = _admin()
    _seed_request(price=10000, pay="paid")
    # viewer: bloqueado pelo @require_role (papel do token)
    assert sales_h.list_sales(_event(a, role="viewer"), None)["statusCode"] == 403
    # admin rebaixado a inativo no banco: assert_active_admin → 403 (fecha a janela de revogação)
    inactive = str(uuid.uuid4())
    _seed_user(inactive, role="admin", status="inactive")
    assert sales_h.list_sales(_event(inactive, role="admin"), None)["statusCode"] == 403


def test_sales_rls_isola_org():
    a = _admin()
    _seed_request(org=SYSTEM_ORG, price=10000, pay="paid")
    _seed_request(org=OTHER_ORG, price=99999, pay="paid")     # outra org
    d = _data(sales_h.list_sales(_event(a), None))            # caller da SYSTEM_ORG
    assert d["total"] == 1 and d["items"][0]["gross_cents"] == 10000


def test_sales_reconcilia_com_overview():
    """A soma das vendas (bruto/recebido/pendente) tem que bater com os KPIs da Visão
    Geral — mesma fonte (requests + buckets). Vale como invariante anti-drift."""
    a = _admin()
    _seed_request(price=30000, pay="paid")
    _seed_request(price=8000, pay="pending")
    _seed_request(price=5000, pay="simulated")
    _seed_request(price=2000, pay="canceled")

    sales = _data(sales_h.list_sales(_event(a), None))["summary"]
    kpis = _data(ovw_h.get_overview(_event(a), None))["kpis"]
    assert sales["gross_cents"] == kpis["gross_cents"] == 45000
    assert sales["received_cents"] == kpis["received_cents"]      # só 'paid'
    assert sales["pending_cents"] == kpis["pending_cents"]
    assert sales["canceled_cents"] == kpis["canceled_cents"]
    assert sales["simulated_cents"] == kpis["simulated_cents"]
    assert sales["count"] == kpis["count"] == 4
