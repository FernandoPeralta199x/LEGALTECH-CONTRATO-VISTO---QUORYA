"""Testes do handler Financeiro — visão geral agregada por organização (PG18 + RLS).

O overview agrega a tabela `requests` (registro de vendas atual). KPIs sem fonte
de dados (tributos, custos de API, notas, margem, receita líquida, atraso) são
honestamente nulos. Isolamento por organização é garantido pela RLS.
"""
from _dbadmin import admin_conn
import json
import uuid

import pytest

from src.handlers import financial as fin_h
from src.handlers import requests as req_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"

_NULL_KPIS = ("overdue_cents", "net_cents", "margin_cents")


def _reset():
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.external_api_costs, public.tax_provisions,"
            " public.fiscal_documents, public.timeline_events, public.triage_modules,"
            " public.case_parties, public.documents, public.requests, public.cases,"
            " public.clients, public.request_code_sequences, public.pricing_configs"
            " RESTART IDENTITY CASCADE")
    conn.close()


def _seed_user(user_id, org=SYSTEM_ORG, role="admin", status="active"):
    """Semeia public.users — o overview financeiro é admin-only e reconsulta o papel
    ATUAL no banco (assert_active_admin, SEC-02b), então um user_id sintético leva 403."""
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
    """user_id de um admin ATIVO e existente no banco (na org indicada)."""
    uid = str(uuid.uuid4())
    _seed_user(uid, org=org, role="admin", status="active")
    return uid


def _event(user_id, role="admin", org_id=SYSTEM_ORG, query=None, body=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {},
        "queryStringParameters": query or {},
    }


def _data(resp):
    assert resp["statusCode"] == 200, resp
    return json.loads(resp["body"])["data"]


def _payload(product_type="analise_contratual"):
    return {
        "product_type": product_type,
        "title": "Venda financeira",
        "parties": [{"name": "Parte A", "role": "contratante"}],
        "document": {"filename": "doc.pdf", "size_bytes": 100},
        "selected_modules": ["ia_deepseek"],
    }


def _prices(org_id=SYSTEM_ORG):
    """[(id, total_price_cents), ...] das requests da org, por ordem de criação."""
    conn = admin_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, total_price_cents FROM public.requests"
            " WHERE organization_id = %s ORDER BY created_at, id", (org_id,))
        rows = cur.fetchall()
    conn.close()
    return rows


def _mark_paid(req_id):
    _set_status(req_id, "paid")


def _set_status(req_id, status):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE public.requests SET payment_status=%s WHERE id=%s", (status, str(req_id)))
    conn.close()


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_overview_vazio():
    a = _admin()
    data = _data(fin_h.get_overview(_event(a), None))
    k = data["kpis"]
    assert k["count"] == 0
    assert k["gross_cents"] == 0
    assert k["received_cents"] == 0
    assert k["pending_cents"] == 0
    assert k["ticket_cents"] is None
    for key in _NULL_KPIS:
        assert k[key] is None, key
    # api_cost/tax/invoices têm fonte (Fases 4-5): vazio = 0, não null
    assert k["api_cost_cents"] == 0
    assert k["tax_cents"] == 0
    assert k["invoices_count"] == 0
    assert data["currency"] == "BRL"
    assert data["period"] == "month"


def test_overview_conta_soma_pendente():
    a = _admin()
    req_h.create_request(_event(a, body=_payload()), None)
    req_h.create_request(_event(a, body=_payload("dados_partes")), None)

    prices = _prices()
    priced = [c for _, c in prices if c is not None]
    gross = sum(priced)
    assert gross > 0  # o pricing produziu valor real

    k = _data(fin_h.get_overview(_event(a), None))["kpis"]
    assert k["count"] == 2
    assert k["gross_cents"] == gross
    assert k["pending_cents"] == gross      # ambas 'pending' por padrão
    assert k["received_cents"] == 0
    assert k["canceled_cents"] == 0
    assert k["refunded_cents"] == 0
    assert k["ticket_cents"] == round(gross / len(priced))


def test_overview_recebido_vs_pendente():
    a = _admin()
    req_h.create_request(_event(a, body=_payload()), None)
    req_h.create_request(_event(a, body=_payload("dados_partes")), None)

    prices = _prices()
    (paid_id, paid_cents), (_, other_cents) = prices[0], prices[1]
    _mark_paid(paid_id)

    k = _data(fin_h.get_overview(_event(a), None))["kpis"]
    assert k["received_cents"] == paid_cents
    assert k["pending_cents"] == other_cents


def test_overview_periodo_exclui_fora_do_intervalo():
    a = _admin()
    req_h.create_request(_event(a, body=_payload()), None)
    # criada agora → não deve aparecer no "mês passado"
    k = _data(fin_h.get_overview(_event(a, query={"period": "lastMonth"}), None))["kpis"]
    assert k["count"] == 0
    assert k["gross_cents"] == 0
    # e aparece no "hoje"
    k_hoje = _data(fin_h.get_overview(_event(a, query={"period": "today"}), None))["kpis"]
    assert k_hoje["count"] == 1


def test_overview_isolado_por_org():
    # `a` vende na SYSTEM_ORG; `b` é admin da OTHER_ORG (semeado lá) e não pode enxergar
    # as vendas de `a`. b precisa ser admin ATIVO na sua org — senão o 0 viria do 403
    # (SEC-02b), não da RLS, e o teste deixaria de provar isolamento por organização.
    a = _admin()
    b = _admin(org=OTHER_ORG)
    req_h.create_request(_event(a, body=_payload()), None)
    k = _data(fin_h.get_overview(_event(b, org_id=OTHER_ORG), None))["kpis"]
    assert k["count"] == 0
    assert k["gross_cents"] == 0


def test_overview_periodo_invalido():
    a = _admin()
    resp = fin_h.get_overview(_event(a, query={"period": "xpto"}), None)
    assert resp["statusCode"] == 400


def test_overview_custom_exige_from_to():
    a = _admin()
    resp = fin_h.get_overview(_event(a, query={"period": "custom"}), None)
    assert resp["statusCode"] == 400


def test_overview_buckets_por_status():
    a = _admin()
    for _ in range(4):
        req_h.create_request(_event(a, body=_payload()), None)
    prices = _prices()
    ids = [pid for pid, _ in prices]
    cents = {pid: c for pid, c in prices}
    _set_status(ids[0], "paid")
    _set_status(ids[1], "canceled")
    _set_status(ids[2], "refunded")
    _set_status(ids[3], "simulated")

    k = _data(fin_h.get_overview(_event(a), None))["kpis"]
    # PRC-01: 'recebido' é só dinheiro REAL (paid); o mock 'simulated' fica à parte.
    assert k["received_cents"] == cents[ids[0]]                   # só paid
    assert k["simulated_cents"] == cents[ids[3]]                  # simulated reportado à parte
    assert k["canceled_cents"] == cents[ids[1]]
    assert k["refunded_cents"] == cents[ids[2]]
    assert k["pending_cents"] == 0


def test_overview_simulated_nunca_conta_como_recebido():
    """PRC-01: um pagamento MOCK (simulated) NUNCA conta como receita recebida — o dinheiro
    nunca entrou. É sempre reportado à parte em simulated_cents, em TODOS os ambientes.
    Sem isso, o Relatório Executivo superdimensionava o recebido (~5,4× nos dados de dev)."""
    a = _admin()
    for _ in range(2):
        req_h.create_request(_event(a, body=_payload()), None)
    ids = [pid for pid, _ in _prices()]
    cents = {pid: c for pid, c in _prices()}
    _set_status(ids[0], "paid")
    _set_status(ids[1], "simulated")

    k = _data(fin_h.get_overview(_event(a), None))["kpis"]
    assert k["received_cents"] == cents[ids[0]], "simulated não pode contar como recebido"
    assert k["simulated_cents"] == cents[ids[1]]
    # reconciliação: bruto = recebido + pendente + cancelado + simulado + reembolsado
    assert k["gross_cents"] == cents[ids[0]] + cents[ids[1]]


def test_overview_custom_valido_inclusivo():
    a = _admin()
    req_h.create_request(_event(a, body=_payload()), None)
    incluso = _data(fin_h.get_overview(
        _event(a, query={"period": "custom", "from": "2020-01-01", "to": "2035-12-31"}), None))["kpis"]
    assert incluso["count"] == 1
    passado = _data(fin_h.get_overview(
        _event(a, query={"period": "custom", "from": "2020-01-01", "to": "2020-12-31"}), None))["kpis"]
    assert passado["count"] == 0


def test_overview_custom_data_invalida():
    a = _admin()
    resp = fin_h.get_overview(
        _event(a, query={"period": "custom", "from": "abc", "to": "2026-01-01"}), None)
    assert resp["statusCode"] == 400


def test_overview_admin_only(monkeypatch):
    """SEC-02b: o Financeiro é admin-only no backend, não só no AdminGuard do frontend.

    Antes da correção, viewer/analyst recebiam 200 (o handler tinha só @require_user).
    Agora: @require_role nega quem não é admin no token; e assert_active_admin nega
    quem virou não-admin no banco depois do login (janela de revogação).
    """
    # (a) papel do token não é admin -> 403 já no decorator, sem tocar no banco
    v = _admin()  # existe no banco, mas o token diz viewer
    assert fin_h.get_overview(_event(v, role="viewer"), None)["statusCode"] == 403
    assert fin_h.get_overview(_event(v, role="analyst"), None)["statusCode"] == 403
    # (b) token diz admin, mas o banco já rebaixou -> 403 pelo recheck ATUAL
    a = _admin()
    assert fin_h.get_overview(_event(a), None)["statusCode"] == 200  # admin real: ok
    _seed_user(a, role="viewer", status="active")
    assert fin_h.get_overview(_event(a), None)["statusCode"] == 403
    # (c) conta desativada -> 403
    _seed_user(a, role="admin", status="inactive")
    assert fin_h.get_overview(_event(a), None)["statusCode"] == 403
