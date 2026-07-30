"""Testes do custo AUTOMÁTICO de APIs — derivado do uso real (triage_modules,
status='done') × tarifa de custo por provedor. Quantidade real; tarifa config.

Cobre: derivação por provedor (com canonização do prefixo mock_), só executados
contam, todos os provedores da tarifa aparecem (0 se sem uso), isolamento por org
(RLS) e janela de período.
"""
from _dbadmin import admin_conn
import json
import uuid

import pytest

from src.handlers import financial_api_costs as ac_h
from src.services.financial.api_costs import API_PROVIDER_TARIFF

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _reset():
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.triage_modules, public.cases RESTART IDENTITY CASCADE")
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


def _seed_case(org=SYSTEM_ORG):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.cases (organization_id, case_type)"
                    " VALUES (%s,'fin') RETURNING id", (org,))
        cid = cur.fetchone()[0]
    conn.close()
    return cid


def _seed_module(org, case_id, provider, status="done", finished_at=None):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.triage_modules"
            " (organization_id, case_id, module_key, module_label, provider, status,"
            "  source_mode, finished_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,'mock', COALESCE(%s, now()))",
            (org, case_id, provider.replace("mock_", ""), provider, provider, status, finished_at))
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


def test_api_automatic_deriva_do_uso_real():
    a = _admin()
    case = _seed_case(SYSTEM_ORG)
    _seed_module(SYSTEM_ORG, case, "mock_escavador", "done")   # 2 consultas
    _seed_module(SYSTEM_ORG, case, "mock_escavador", "done")
    _seed_module(SYSTEM_ORG, case, "mock_serasa", "done")      # 1 consulta
    _seed_module(SYSTEM_ORG, case, "mock_escavador", "not_started")  # NÃO executado → não conta

    auto = _data(ac_h.list_api_costs(_event(a), None))["automatic"]
    prov = {p["provider"]: p for p in auto["by_provider"]}
    tar = API_PROVIDER_TARIFF
    assert prov["escavador"]["quantity"] == 2
    assert prov["escavador"]["total_cents"] == 2 * tar["escavador"]["unit_cost_cents"]
    assert prov["serasa"]["quantity"] == 1
    # todos os provedores da tarifa aparecem (0 quando sem uso — honesto)
    assert prov["targetdata"]["quantity"] == 0 and prov["procon"]["quantity"] == 0
    assert auto["calls"] == 3
    assert auto["total_cents"] == prov["escavador"]["total_cents"] + prov["serasa"]["total_cents"]
    assert "disclaimer" in auto and prov["escavador"]["provider_label"] == "Escavador"


def test_api_automatic_isolado_por_org():
    a = _admin()
    case = _seed_case(SYSTEM_ORG)
    _seed_module(SYSTEM_ORG, case, "mock_escavador", "done")
    b = _admin(org=OTHER_ORG)
    auto = _data(ac_h.list_api_costs(_event(b, org_id=OTHER_ORG), None))["automatic"]
    assert auto["total_cents"] == 0 and auto["calls"] == 0


def test_api_automatic_periodo():
    a = _admin()
    case = _seed_case(SYSTEM_ORG)
    _seed_module(SYSTEM_ORG, case, "mock_escavador", "done")  # now → today/month
    assert _data(ac_h.list_api_costs(_event(a, query={"period": "lastMonth"}), None))["automatic"]["calls"] == 0
    assert _data(ac_h.list_api_costs(_event(a, query={"period": "today"}), None))["automatic"]["calls"] == 1
