"""Testes do handler de Gastos com APIs (Fase 4) — external_api_costs (PG18 + RLS).

Cobre: agregação/listagem, total calculado pelo banco (anti-forja), validação de
centavos, admin-only + reconsulta de revogação (assert_active_admin), isolamento
por organização (RLS), vínculo FK org-scoped, período, auditoria e integração com
o overview (api_cost_cents deixa de ser null).
"""
from _dbadmin import admin_conn
import json
import uuid

import pytest

from src.handlers import financial as fin_h
from src.handlers import financial_api_costs as ac_h
from src.handlers import requests as req_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _reset():
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.external_api_costs, public.timeline_events, public.triage_modules,"
            " public.case_parties, public.documents, public.requests, public.cases,"
            " public.clients, public.request_code_sequences, public.pricing_configs"
            " RESTART IDENTITY CASCADE")
    conn.close()


def _seed_user(user_id, org=SYSTEM_ORG, role="admin", status="active"):
    """Semeia public.users — necessário porque o POST reconsulta o papel ATUAL
    no banco (assert_active_admin) para fechar a janela de revogação do JWT."""
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


def _event(user_id, role="admin", org_id=SYSTEM_ORG, query=None, body=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {},
        "queryStringParameters": query or {},
    }


def _data(resp, code=200):
    assert resp["statusCode"] == code, resp
    return json.loads(resp["body"]).get("data")


def _cost(**over):
    base = {"provider": "serasa", "operation": "consulta_cpf", "quantity": 1, "unit_cost_cents": 1500}
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_api_costs_vazio():
    a = _admin()
    data = _data(ac_h.list_api_costs(_event(a), None))
    assert data["items"] == []
    assert data["total"] == 0
    assert data["summary"]["api_cost_cents"] == 0
    assert data["summary"]["by_provider"] == []


def test_api_costs_criar_e_listar():
    a = _admin()
    created = _data(ac_h.create_api_cost(
        _event(a, body=_cost(quantity=3, unit_cost_cents=1500)), None), code=201)
    assert created["total_cost_cents"] == 4500   # o banco calcula 3 * 1500

    data = _data(ac_h.list_api_costs(_event(a), None))
    assert data["total"] == 1
    assert data["items"][0]["provider"] == "serasa"
    assert data["summary"]["api_cost_cents"] == 4500
    assert data["summary"]["by_provider"][0] == {"provider": "serasa", "total_cents": 4500}


def test_api_costs_total_e_pelo_banco_rejeita_forja():
    a = _admin()
    # extra="forbid" rejeita total_cost_cents no corpo
    resp = ac_h.create_api_cost(_event(a, body=_cost(total_cost_cents=1)), None)
    assert resp["statusCode"] == 400


def test_api_costs_valida_centavos():
    a = _admin()
    assert ac_h.create_api_cost(_event(a, body=_cost(unit_cost_cents=-1)), None)["statusCode"] == 400
    assert ac_h.create_api_cost(_event(a, body=_cost(quantity=0)), None)["statusCode"] == 400
    assert ac_h.create_api_cost(_event(a, body=_cost(unit_cost_cents=100000001)), None)["statusCode"] == 400
    assert ac_h.create_api_cost(_event(a, body=_cost(provider="")), None)["statusCode"] == 400


def test_api_costs_previsto_vs_processado():
    a = _admin()
    ac_h.create_api_cost(_event(a, body=_cost(unit_cost_cents=1000, status="processado")), None)
    ac_h.create_api_cost(_event(a, body=_cost(unit_cost_cents=700, status="previsto")), None)
    s = _data(ac_h.list_api_costs(_event(a), None))["summary"]
    assert s["api_cost_cents"] == 1000            # só processado entra no custo real
    assert s["api_cost_forecast_cents"] == 700    # previsto exposto à parte


def test_api_costs_admin_only():
    a = _admin()
    assert ac_h.create_api_cost(_event(a, role="viewer", body=_cost()), None)["statusCode"] == 403
    assert ac_h.create_api_cost(_event(a, role="analyst", body=_cost()), None)["statusCode"] == 403


def test_api_costs_revogacao_403():
    # token diz admin, mas o usuário está inativo no banco → assert_active_admin → 403
    uid = str(uuid.uuid4())
    _seed_user(uid, role="admin", status="inactive")
    assert ac_h.create_api_cost(_event(uid, body=_cost()), None)["statusCode"] == 403
    # rebaixado a analyst no banco → 403
    _seed_user(uid, role="analyst", status="active")
    assert ac_h.create_api_cost(_event(uid, body=_cost()), None)["statusCode"] == 403
    # usuário inexistente no banco → 403
    ghost = str(uuid.uuid4())
    assert ac_h.create_api_cost(_event(ghost, body=_cost()), None)["statusCode"] == 403


def test_api_costs_isolado_por_org():
    a = _admin()
    ac_h.create_api_cost(_event(a, body=_cost()), None)
    b = _admin(org=OTHER_ORG)  # admin ATIVO na outra org: o 0 vem da RLS, nao do 403 (SEC-02b)
    data = _data(ac_h.list_api_costs(_event(b, org_id=OTHER_ORG), None))
    assert data["total"] == 0
    assert data["summary"]["api_cost_cents"] == 0


def test_api_costs_vinculo_invalido():
    a = _admin()
    # case_id inexistente na org → FK composta viola → 400 (não 500)
    resp = ac_h.create_api_cost(_event(a, body=_cost(case_id=str(uuid.uuid4()))), None)
    assert resp["statusCode"] == 400


def test_api_costs_incurred_at_invalido():
    a = _admin()
    resp = ac_h.create_api_cost(_event(a, body=_cost(incurred_at="not-a-date")), None)
    assert resp["statusCode"] == 400


def test_api_costs_external_id_duplicado():
    a = _admin()
    assert ac_h.create_api_cost(_event(a, body=_cost(external_id="INV-1")), None)["statusCode"] == 201
    # mesmo (provider, external_id) na org → índice único → 409 (não 400 de vínculo)
    assert ac_h.create_api_cost(_event(a, body=_cost(external_id="INV-1")), None)["statusCode"] == 409


def test_api_costs_vinculo_valido_a_uma_venda():
    a = _admin()
    req_h.create_request(_event(a, body={
        "product_type": "analise_contratual", "title": "V",
        "parties": [{"name": "P", "role": "contratante"}],
        "document": {"filename": "d.pdf", "size_bytes": 10},
        "selected_modules": ["ia_deepseek"],
    }), None)
    conn = admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM public.requests WHERE organization_id=%s LIMIT 1", (SYSTEM_ORG,))
        req_id = str(cur.fetchone()[0])
    conn.close()
    created = _data(ac_h.create_api_cost(_event(a, body=_cost(request_id=req_id)), None), code=201)
    assert created["request_id"] == req_id


def test_api_costs_periodo_exclui():
    a = _admin()
    ac_h.create_api_cost(_event(a, body=_cost()), None)
    passado = _data(ac_h.list_api_costs(_event(a, query={"period": "lastMonth"}), None))
    assert passado["total"] == 0
    assert passado["summary"]["api_cost_cents"] == 0


def test_api_costs_auditoria():
    a = _admin()
    created = _data(ac_h.create_api_cost(_event(a, body=_cost()), None), code=201)
    conn = admin_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit.audit_log WHERE resource_type='external_api_costs'"
            " AND resource_id=%s AND action='INSERT'", (created["id"],))
        n = cur.fetchone()[0]
    conn.close()
    assert n == 1


def test_overview_integra_api_cost():
    a = _admin()
    ac_h.create_api_cost(_event(a, body=_cost(unit_cost_cents=2500, status="processado")), None)
    kpis = _data(fin_h.get_overview(_event(a), None))["kpis"]
    assert kpis["api_cost_cents"] == 2500          # deixou de ser None
    assert kpis["api_cost_forecast_cents"] == 0
