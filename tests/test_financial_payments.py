"""Testes da aba Pagamentos — projeção sobre requests com installment_plan.

Cobre: vazio coerente, projeção do jsonb (id/venda/cliente/método/valor/parcelas/
status/data), SÓ vendas com pagamento registrado, taxa=0 e líquido=valor, rótulo de
método, cliente não-atribuído, ordenação por data do pagamento, paginação, busca q,
KPIs do período (não filtrados pela busca), RBAC admin-only + revogação, RLS.
"""
from _dbadmin import admin_conn
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest

from src.handlers import financial_payments as pay_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _recent(hours_ago=0):
    """requested_at recente (ISO/UTC) — a aba filtra pela DATA DO PAGAMENTO, então os
    seeds precisam cair no período corrente (default = mês)."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


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


def _seed_payment(org=SYSTEM_ORG, price=10000, pay="simulated", case_id=None, code=None,
                  method="pix", parcelas=1, valor_total=None, ext_ref=None, requested_at=None):
    """Insere um request COM installment_plan (= pagamento registrado)."""
    valor_total = valor_total if valor_total is not None else price
    plan = {
        "version": 1, "method": method, "parcelas": parcelas,
        "valor_total_cents": valor_total,
        "payment": {
            "provider": "mock", "mode": "mock", "status": pay, "method": method,
            "external_reference": ext_ref or ("mock_" + uuid.uuid4().hex[:12]),
            "requested_at": requested_at or _recent(),
        },
    }
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.requests"
            " (organization_id, created_by, code, product_type, product_label, title,"
            "  status, source_mode, total_price_cents, payment_status, installment_plan, case_id)"
            " VALUES (%s, gen_random_uuid(), %s, 'analise_contratual', 'Análise', 'T',"
            "         'created', 'manual', %s, %s, %s::jsonb, %s) RETURNING id",
            (org, code or ("R-" + uuid.uuid4().hex[:10]), price, pay, json.dumps(plan), case_id))
        rid = cur.fetchone()[0]
    conn.close()
    return rid


def _seed_request_sem_pagamento(org=SYSTEM_ORG, price=5000, code=None):
    """Venda sem pagamento (installment_plan NULL) — NÃO deve aparecer em Pagamentos."""
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.requests"
            " (organization_id, created_by, code, product_type, product_label, title,"
            "  status, source_mode, total_price_cents, payment_status)"
            " VALUES (%s, gen_random_uuid(), %s, 'analise_contratual', 'Análise', 'T',"
            "         'created', 'manual', %s, 'pending')",
            (org, code or ("R-" + uuid.uuid4().hex[:10]), price))
    conn.close()


def _event(user_id, role="admin", org_id=SYSTEM_ORG, query=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "perfil": "administrador", "organization_id": org_id}},
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


def test_payments_vazio():
    a = _admin()
    d = _data(pay_h.list_payments(_event(a), None))
    assert d["items"] == [] and d["total"] == 0
    assert d["summary"]["count"] == 0 and d["summary"]["total_cents"] == 0
    assert d["summary"]["average_cents"] is None


def test_payments_projecao_do_jsonb():
    a = _admin()
    cid = _seed_client(SYSTEM_ORG, "Cliente A")
    case = _seed_case(SYSTEM_ORG, cid)
    ra = _recent(1)
    _seed_payment(price=23700, pay="paid", case_id=case, code="R-PAY", method="pix",
                  valor_total=23700, ext_ref="mock_ref_123", requested_at=ra)
    d = _data(pay_h.list_payments(_event(a), None))
    assert d["total"] == 1
    row = d["items"][0]
    assert row["id"] == "mock_ref_123"            # id da cobrança (external_reference)
    assert row["code"] == "R-PAY"                 # venda
    assert row["client_name"] == "Cliente A"
    assert row["method"] == "pix" and row["method_label"] == "Pix"
    assert row["installments"] == 1
    assert row["amount_cents"] == 23700           # valor cobrado (do jsonb)
    assert row["fee_cents"] == 0                  # mock: sem taxa de gateway
    assert row["net_cents"] == 23700              # líquido = valor
    assert row["payment_status"] == "paid"
    assert row["paid_at"] == ra                   # data do pagamento (requested_at)


def test_payments_so_requests_com_plano():
    a = _admin()
    _seed_payment(price=10000, pay="simulated")           # tem plano → aparece
    _seed_request_sem_pagamento(price=5000)               # sem plano → NÃO aparece
    d = _data(pay_h.list_payments(_event(a), None))
    assert d["total"] == 1
    assert d["summary"]["count"] == 1                     # só o pagamento registrado


@pytest.mark.parametrize("method,label", [
    ("pix", "Pix"), ("boleto", "Boleto"), ("cartao", "Cartão"), ("debito", "Débito"),
])
def test_payments_rotulo_metodo(method, label):
    a = _admin()
    _seed_payment(price=10000, pay="paid", method=method)
    row = _data(pay_h.list_payments(_event(a), None))["items"][0]
    assert row["method"] == method and row["method_label"] == label


def test_payments_valor_cobrado_com_juros():
    # valor_total_cents (com juros) difere do preço da venda — é o valor COBRADO
    a = _admin()
    _seed_payment(price=10000, pay="paid", parcelas=3, valor_total=11000)  # juros
    row = _data(pay_h.list_payments(_event(a), None))["items"][0]
    assert row["amount_cents"] == 11000 and row["net_cents"] == 11000
    assert row["installments"] == 3


def test_payments_cliente_nao_atribuido():
    a = _admin()
    _seed_payment(price=8000, pay="simulated", case_id=None)   # sem caso → sem cliente
    row = _data(pay_h.list_payments(_event(a), None))["items"][0]
    assert row["client_name"] is None and row["client_id"] is None


def test_payments_ordena_por_data_do_pagamento():
    a = _admin()
    _seed_payment(price=1000, pay="paid", code="R-OLD", requested_at=_recent(3))
    _seed_payment(price=2000, pay="paid", code="R-NEW", requested_at=_recent(1))
    items = _data(pay_h.list_payments(_event(a), None))["items"]
    assert items[0]["code"] == "R-NEW"   # pagamento mais recente primeiro
    assert items[1]["code"] == "R-OLD"


def test_payments_periodo_por_data_do_pagamento():
    """Fluxo de caixa: o período filtra pela DATA DO PAGAMENTO, não pela criação da
    venda. Ambos os pedidos são criados AGORA (created_at=now), mas só o PAGO no período
    corrente aparece — provando que o filtro é por requested_at, não por created_at."""
    a = _admin()
    _seed_payment(price=10000, pay="paid", code="R-PAGO-AGORA", requested_at=_recent(1))
    _seed_payment(price=20000, pay="paid", code="R-ANTIGO", requested_at=_recent(24 * 45))  # ~45 dias atrás
    d = _data(pay_h.list_payments(_event(a), None))   # período default = mês
    assert d["total"] == 1 and d["items"][0]["code"] == "R-PAGO-AGORA"
    assert d["summary"]["count"] == 1                 # KPIs também por data do pagamento


def test_payments_paginacao():
    a = _admin()
    for i in range(5):
        _seed_payment(price=1000 * (i + 1), pay="paid", code=f"R-{i}",
                      requested_at=_recent(i))
    d = _data(pay_h.list_payments(_event(a, query={"page": "1", "page_size": "2"}), None))
    assert len(d["items"]) == 2 and d["total"] == 5 and d["total_pages"] == 3
    assert d["summary"]["count"] == 5 and d["summary"]["total_cents"] == 15000


def test_payments_busca_q():
    a = _admin()
    cid = _seed_client(SYSTEM_ORG, "Acme Corp")
    case = _seed_case(SYSTEM_ORG, cid)
    _seed_payment(price=10000, pay="paid", case_id=case, code="R-ACME", method="boleto")
    _seed_payment(price=20000, pay="paid", code="R-XYZ", method="pix")
    # por código
    assert _data(pay_h.list_payments(_event(a, query={"q": "ACME"}), None))["total"] == 1
    # por cliente
    d = _data(pay_h.list_payments(_event(a, query={"q": "acme corp"}), None))
    assert d["total"] == 1 and d["items"][0]["client_name"] == "Acme Corp"
    # por método
    d = _data(pay_h.list_payments(_event(a, query={"q": "boleto"}), None))
    assert d["total"] == 1 and d["items"][0]["method"] == "boleto"


def test_payments_busca_filtra_tabela_mas_nao_os_kpis():
    """Honestidade: a busca filtra a LISTA; os KPIs continuam do PERÍODO inteiro."""
    a = _admin()
    _seed_payment(price=10000, pay="paid", code="R-ACME")
    _seed_payment(price=20000, pay="paid", code="R-XYZ")
    d = _data(pay_h.list_payments(_event(a, query={"q": "ACME"}), None))
    assert d["total"] == 1                        # a LISTA é filtrada
    assert d["summary"]["count"] == 2             # KPIs do período
    assert d["summary"]["total_cents"] == 30000


def test_payments_summary_recebido_e_simulado():
    a = _admin()
    _seed_payment(price=30000, pay="paid")
    _seed_payment(price=5000, pay="simulated")
    s = _data(pay_h.list_payments(_event(a), None))["summary"]
    assert s["count"] == 2 and s["total_cents"] == 35000
    assert s["received_cents"] == 30000           # só 'paid'
    assert s["simulated_cents"] == 5000           # mock, à parte (PRC-01)


def test_payments_admin_only_e_revogacao():
    a = _admin()
    _seed_payment(price=10000, pay="paid")
    assert pay_h.list_payments(_event(a, role="viewer"), None)["statusCode"] == 403
    inactive = str(uuid.uuid4())
    _seed_user(inactive, role="admin", status="inactive")
    assert pay_h.list_payments(_event(inactive, role="admin"), None)["statusCode"] == 403


def test_payments_rls_isola_org():
    a = _admin()
    _seed_payment(org=SYSTEM_ORG, price=10000, pay="paid")
    _seed_payment(org=OTHER_ORG, price=99999, pay="paid")
    d = _data(pay_h.list_payments(_event(a), None))
    assert d["total"] == 1 and d["items"][0]["amount_cents"] == 10000
