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

_NULL_KPIS = ("overdue_cents", "net_cents", "api_cost_cents", "tax_cents",
              "invoices_count", "margin_cents")


def _reset():
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.timeline_events, public.triage_modules, public.case_parties,"
            " public.documents, public.requests, public.cases, public.clients,"
            " public.request_code_sequences, public.pricing_configs RESTART IDENTITY CASCADE")
    conn.close()


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
    a = str(uuid.uuid4())
    data = _data(fin_h.get_overview(_event(a), None))
    k = data["kpis"]
    assert k["count"] == 0
    assert k["gross_cents"] == 0
    assert k["received_cents"] == 0
    assert k["pending_cents"] == 0
    assert k["ticket_cents"] is None
    for key in _NULL_KPIS:
        assert k[key] is None, key
    assert data["currency"] == "BRL"
    assert data["period"] == "month"


def test_overview_conta_soma_pendente():
    a = str(uuid.uuid4())
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
    a = str(uuid.uuid4())
    req_h.create_request(_event(a, body=_payload()), None)
    req_h.create_request(_event(a, body=_payload("dados_partes")), None)

    prices = _prices()
    (paid_id, paid_cents), (_, other_cents) = prices[0], prices[1]
    _mark_paid(paid_id)

    k = _data(fin_h.get_overview(_event(a), None))["kpis"]
    assert k["received_cents"] == paid_cents
    assert k["pending_cents"] == other_cents


def test_overview_periodo_exclui_fora_do_intervalo():
    a = str(uuid.uuid4())
    req_h.create_request(_event(a, body=_payload()), None)
    # criada agora → não deve aparecer no "mês passado"
    k = _data(fin_h.get_overview(_event(a, query={"period": "lastMonth"}), None))["kpis"]
    assert k["count"] == 0
    assert k["gross_cents"] == 0
    # e aparece no "hoje"
    k_hoje = _data(fin_h.get_overview(_event(a, query={"period": "today"}), None))["kpis"]
    assert k_hoje["count"] == 1


def test_overview_isolado_por_org():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    req_h.create_request(_event(a, body=_payload()), None)
    # outra organização não enxerga as vendas (RLS)
    k = _data(fin_h.get_overview(_event(b, org_id=OTHER_ORG), None))["kpis"]
    assert k["count"] == 0
    assert k["gross_cents"] == 0


def test_overview_periodo_invalido():
    a = str(uuid.uuid4())
    resp = fin_h.get_overview(_event(a, query={"period": "xpto"}), None)
    assert resp["statusCode"] == 400


def test_overview_custom_exige_from_to():
    a = str(uuid.uuid4())
    resp = fin_h.get_overview(_event(a, query={"period": "custom"}), None)
    assert resp["statusCode"] == 400


def test_overview_buckets_por_status():
    a = str(uuid.uuid4())
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
    assert k["received_cents"] == cents[ids[0]] + cents[ids[3]]   # paid + simulated
    assert k["canceled_cents"] == cents[ids[1]]
    assert k["refunded_cents"] == cents[ids[2]]
    assert k["pending_cents"] == 0


def test_overview_custom_valido_inclusivo():
    a = str(uuid.uuid4())
    req_h.create_request(_event(a, body=_payload()), None)
    incluso = _data(fin_h.get_overview(
        _event(a, query={"period": "custom", "from": "2020-01-01", "to": "2035-12-31"}), None))["kpis"]
    assert incluso["count"] == 1
    passado = _data(fin_h.get_overview(
        _event(a, query={"period": "custom", "from": "2020-01-01", "to": "2020-12-31"}), None))["kpis"]
    assert passado["count"] == 0


def test_overview_custom_data_invalida():
    a = str(uuid.uuid4())
    resp = fin_h.get_overview(
        _event(a, query={"period": "custom", "from": "abc", "to": "2026-01-01"}), None)
    assert resp["statusCode"] == 400
