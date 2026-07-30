"""Testes do handler de Tributos (Fase 5) — tax_provisions (PG18 + RLS).

Controle interno: valor manual do admin; o banco só calcula divergence_cents
(confirmado - estimado). Cobre agregação, divergência GERADA, anti-forja,
admin+revogação, RLS, período (competência), FK e auditoria.
"""
from _dbadmin import admin_conn
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.handlers import financial as fin_h
from src.handlers import financial_fiscal as ff_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"
_BR = timezone(timedelta(hours=-3))
_NOW = datetime.now(_BR)  # competência do mês corrente → cai no período "month"


def _reset():
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.tax_provisions RESTART IDENTITY CASCADE")
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


def _event(user_id, role="admin", org_id=SYSTEM_ORG, query=None, body=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "perfil": "administrador", "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {},
        "queryStringParameters": query or {},
    }


def _data(resp, code=200):
    assert resp["statusCode"] == code, resp
    return json.loads(resp["body"]).get("data")


def _tax(**over):
    base = {"tax_type": "iss", "estimated_amount_cents": 5000,
            "competencia_ano": _NOW.year, "competencia_mes": _NOW.month}
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_taxes_vazio():
    a = _admin()
    d = _data(ff_h.list_taxes(_event(a), None))
    assert d["items"] == []
    assert d["total"] == 0
    assert d["summary"]["tax_cents"] == 0
    assert d["summary"]["by_type"] == []
    assert "disclaimer" in d


def test_taxes_criar_e_listar():
    a = _admin()
    created = _data(ff_h.create_tax(_event(a, body=_tax(estimated_amount_cents=5000)), None), code=201)
    assert created["status"] == "estimado"
    d = _data(ff_h.list_taxes(_event(a), None))
    assert d["total"] == 1
    assert d["summary"]["tax_cents"] == 5000       # sem confirmado → usa estimado
    assert d["summary"]["by_type"][0] == {"tax_type": "iss", "total_cents": 5000}


def test_taxes_confirmado_gera_divergencia():
    a = _admin()
    created = _data(ff_h.create_tax(_event(a, body=_tax(
        estimated_amount_cents=1000, confirmed_amount_cents=1200, status="confirmado")), None), code=201)
    assert created["divergence_cents"] == 200      # 1200 - 1000, GERADO pelo banco
    d = _data(ff_h.list_taxes(_event(a), None))["summary"]
    assert d["tax_cents"] == 1200                   # confirmado vence estimado
    assert d["tax_confirmed_cents"] == 1200
    assert d["tax_estimated_cents"] == 1000


def test_taxes_confirmado_exige_valor():
    a = _admin()
    # status confirmado sem confirmed_amount_cents → 400 (model_validator)
    assert ff_h.create_tax(_event(a, body=_tax(status="confirmado")), None)["statusCode"] == 400


def test_taxes_divergente_exige_confirmado():
    a = _admin()
    assert ff_h.create_tax(_event(a, body=_tax(status="divergente")), None)["statusCode"] == 400
    ok = ff_h.create_tax(_event(a, body=_tax(status="divergente", confirmed_amount_cents=6000)), None)
    assert ok["statusCode"] == 201


def test_taxes_rejeita_forja():
    a = _admin()
    # divergence_cents / currency no corpo → 400 (extra=forbid)
    assert ff_h.create_tax(_event(a, body=_tax(divergence_cents=1)), None)["statusCode"] == 400
    assert ff_h.create_tax(_event(a, body=_tax(currency="USD")), None)["statusCode"] == 400


def test_taxes_valida():
    a = _admin()
    assert ff_h.create_tax(_event(a, body=_tax(estimated_amount_cents=-1)), None)["statusCode"] == 400
    assert ff_h.create_tax(_event(a, body=_tax(rate_bps=10001)), None)["statusCode"] == 400
    assert ff_h.create_tax(_event(a, body=_tax(competencia_mes=13)), None)["statusCode"] == 400
    assert ff_h.create_tax(_event(a, body=_tax(tax_type="xpto")), None)["statusCode"] == 400


def test_taxes_exclui_cancelado_do_kpi():
    a = _admin()
    ff_h.create_tax(_event(a, body=_tax(estimated_amount_cents=1000, status="estimado")), None)
    ff_h.create_tax(_event(a, body=_tax(estimated_amount_cents=9999, status="cancelado")), None)
    assert _data(ff_h.list_taxes(_event(a), None))["summary"]["tax_cents"] == 1000


def test_taxes_admin_only():
    a = _admin()
    assert ff_h.create_tax(_event(a, role="viewer", body=_tax()), None)["statusCode"] == 403
    assert ff_h.create_tax(_event(a, role="analyst", body=_tax()), None)["statusCode"] == 403


def test_taxes_revogacao_403():
    uid = str(uuid.uuid4())
    _seed_user(uid, status="inactive")
    assert ff_h.create_tax(_event(uid, body=_tax()), None)["statusCode"] == 403
    ghost = str(uuid.uuid4())
    assert ff_h.create_tax(_event(ghost, body=_tax()), None)["statusCode"] == 403


def test_taxes_isolado_por_org():
    a = _admin()
    ff_h.create_tax(_event(a, body=_tax()), None)
    b = _admin(org=OTHER_ORG)  # admin ATIVO na outra org: o 0 vem da RLS, nao do 403 (SEC-02b)
    d = _data(ff_h.list_taxes(_event(b, org_id=OTHER_ORG), None))
    assert d["total"] == 0 and d["summary"]["tax_cents"] == 0


def test_taxes_vinculo_invalido():
    a = _admin()
    assert ff_h.create_tax(_event(a, body=_tax(case_id=str(uuid.uuid4()))), None)["statusCode"] == 400


def test_taxes_periodo_por_competencia():
    a = _admin()
    ff_h.create_tax(_event(a, body=_tax()), None)                       # mês corrente
    ff_h.create_tax(_event(a, body=_tax(competencia_ano=2020, competencia_mes=1)), None)
    # custom cobrindo só 2020 → só o de jan/2020
    d = _data(ff_h.list_taxes(_event(a, query={"period": "custom", "from": "2020-01-01", "to": "2020-12-31"}), None))
    assert d["total"] == 1


def test_taxes_auditoria():
    a = _admin()
    created = _data(ff_h.create_tax(_event(a, body=_tax()), None), code=201)
    conn = admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit.audit_log WHERE resource_type='tax_provisions'"
                    " AND resource_id=%s AND action='INSERT'", (created["id"],))
        n = cur.fetchone()[0]
    conn.close()
    assert n == 1


def test_overview_integra_tax_cents():
    a = _admin()
    ff_h.create_tax(_event(a, body=_tax(
        estimated_amount_cents=1000, confirmed_amount_cents=2500, status="confirmado")), None)
    kpis = _data(fin_h.get_overview(_event(a), None))["kpis"]
    assert kpis["tax_cents"] == 2500               # deixou de ser None
    assert kpis["tax_confirmed_cents"] == 2500
