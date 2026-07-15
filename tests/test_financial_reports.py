"""Testes do Relatório Executivo Financeiro (Fase 6) — consolidação read-only.

O handler consolida os agregados das Fases 3-5 (overview + custos API + tributos +
notas) num DTO. Cobre: vazio coerente, consolidação com dados, RLS por org,
período, metadata generated_by (honesto), validação e trilha de auditoria (log).
"""
from _dbadmin import admin_conn
import json
import logging
import uuid

import pytest

from src.handlers import financial_api_costs as ac_h
from src.handlers import financial_fiscal as ff_h
from src.handlers import financial_reports as rep_h
from src.handlers import requests as req_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


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


def _event(user_id, role="admin", org_id=SYSTEM_ORG, email="u@t.c", query=None, body=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": email,
                                          "role": role, "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {},
        "queryStringParameters": query or {},
    }


def _data(resp, code=200):
    assert resp["statusCode"] == code, resp
    return json.loads(resp["body"]).get("data")


def _sale_payload(product_type="analise_contratual"):
    return {
        "product_type": product_type, "title": "Venda relatório",
        "parties": [{"name": "P", "role": "contratante"}],
        "document": {"filename": "d.pdf", "size_bytes": 10},
        "selected_modules": ["ia_deepseek"],
    }


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_report_vazio_coerente():
    a = _admin()
    d = _data(rep_h.get_executive_report(_event(a), None))
    ov = d["overview"]
    assert ov["count"] == 0 and ov["gross_cents"] == 0
    assert ov["ticket_cents"] is None
    assert ov["overdue_cents"] is None and ov["net_cents"] is None and ov["margin_cents"] is None
    # KPIs com fonte (Fases 4-5) → 0, não null
    assert ov["api_cost_cents"] == 0 and ov["tax_cents"] == 0 and ov["invoices_count"] == 0
    assert d["api_costs"]["summary"]["api_cost_cents"] == 0 and d["api_costs"]["by_provider"] == []
    assert d["taxes"]["summary"]["tax_cents"] == 0 and d["taxes"]["by_type"] == []
    assert d["fiscal"]["summary"]["invoices_count"] == 0 and d["fiscal"]["by_status"] == []
    assert d["metadata"]["currency"] == "BRL" and d["metadata"]["period"] == "month"
    assert isinstance(d["disclaimers"], list) and len(d["disclaimers"]) >= 3


def test_report_consolida_todas_secoes():
    a = _admin()
    req_h.create_request(_event(a, body=_sale_payload()), None)
    ac_h.create_api_cost(_event(a, body={"provider": "deepseek", "operation": "llm",
                                         "quantity": 2, "unit_cost_cents": 1500}), None)
    ff_h.create_tax(_event(a, body={"tax_type": "iss", "estimated_amount_cents": 6400,
                                    "status": "estimado", "competencia_ano": 2026,
                                    "competencia_mes": 7}), None)
    ff_h.create_fiscal_document(_event(a, body={"doc_type": "nfse", "status": "authorized",
                                                "numero": "1", "amount_cents": 50000}), None)

    d = _data(rep_h.get_executive_report(_event(a), None))
    assert d["overview"]["count"] == 1
    assert d["api_costs"]["by_provider"][0] == {"provider": "deepseek", "total_cents": 3000}
    assert d["taxes"]["by_type"][0]["tax_type"] == "iss"
    assert {"status": "authorized", "count": 1} in d["fiscal"]["by_status"]
    # consistência: o KPI do overview bate com a seção de custos
    assert d["overview"]["api_cost_cents"] == d["api_costs"]["summary"]["api_cost_cents"] == 3000


def test_report_isolado_por_org():
    a = _admin()
    req_h.create_request(_event(a, body=_sale_payload()), None)
    b = str(uuid.uuid4())
    d = _data(rep_h.get_executive_report(_event(b, org_id=OTHER_ORG), None))
    assert d["overview"]["count"] == 0
    assert d["api_costs"]["by_provider"] == [] and d["taxes"]["by_type"] == []


def test_report_periodo_exclui_fora():
    a = _admin()
    req_h.create_request(_event(a, body=_sale_payload()), None)
    assert _data(rep_h.get_executive_report(_event(a, query={"period": "lastMonth"}), None))["overview"]["count"] == 0
    assert _data(rep_h.get_executive_report(_event(a, query={"period": "today"}), None))["overview"]["count"] == 1


def test_report_metadata_generated_by():
    a = _admin()
    d = _data(rep_h.get_executive_report(_event(a, email="quem@org.com"), None))
    gb = d["metadata"]["generated_by"]
    assert gb["user_id"] == a and gb["email"] == "quem@org.com" and gb["role"] == "admin"
    assert d["metadata"]["generated_at"]
    assert d["metadata"]["filters"]["period"] == "month"


def test_report_generated_by_email_vazio_vira_none():
    a = _admin()
    d = _data(rep_h.get_executive_report(_event(a, email=""), None))
    assert d["metadata"]["generated_by"]["email"] is None


def test_report_periodo_invalido():
    a = _admin()
    assert rep_h.get_executive_report(_event(a, query={"period": "xpto"}), None)["statusCode"] == 400


def test_report_custom_exige_from_to():
    a = _admin()
    assert rep_h.get_executive_report(_event(a, query={"period": "custom"}), None)["statusCode"] == 400
    d = _data(rep_h.get_executive_report(
        _event(a, query={"period": "custom", "from": "2020-01-01", "to": "2020-12-31"}), None))
    assert d["metadata"]["filters"]["from"] == "2020-01-01"


def test_report_preset_ignora_from_to_espurios():
    # Regressão: em período preset, from/to na query são ignorados por _resolve_range,
    # então metadata.filters.from/to devem vir None (sem "filtro aplicado" falso).
    a = _admin()
    d = _data(rep_h.get_executive_report(
        _event(a, query={"period": "month", "from": "2020-01-01", "to": "2020-12-31"}), None))
    f = d["metadata"]["filters"]
    assert f["period"] == "month"
    assert f["from"] is None and f["to"] is None
    # e o range continua sendo o do preset (mês corrente), não 2020 — params espúrios ignorados
    assert d["metadata"]["range"]["from"][:4] != "2020"


def test_report_audit_log_emitido(caplog):
    a = _admin()
    with caplog.at_level(logging.INFO):
        rep_h.get_executive_report(_event(a), None)
    events = []
    for rec in caplog.records:
        try:
            payload = json.loads(rec.getMessage())
        except (ValueError, TypeError):
            continue
        if payload.get("event") == "FINANCIAL_REPORT_GENERATED":
            events.append(payload)
    assert events, "log de auditoria FINANCIAL_REPORT_GENERATED não emitido"
    assert events[-1]["user_id"] == a and events[-1]["organization_id"] == SYSTEM_ORG
    assert events[-1]["period"] == "month" and "range" in events[-1]
