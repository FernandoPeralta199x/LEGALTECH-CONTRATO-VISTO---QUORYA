"""Fase 2 — testes de integração dos handlers cases/case_results (PG18 + RLS).

Invoca os handlers com o `event` que o API Gateway entregaria (com o contexto do
JWT Authorizer), validando RLS, isolamento, gate de case visível e auth.
"""
import json
import uuid

import psycopg2
import pytest

from src.handlers import case_results as cr_h
from src.handlers import cases as cases_h


def _admin_conn():
    return psycopg2.connect(
        host="localhost", port=5433, user="dbadmin",
        password="localdev_cv", dbname="contrato_visto", connect_timeout=5,
    )


def _reset_and_seed_client() -> str:
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.cases, public.clients RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE audit.audit_log RESTART IDENTITY")
        cur.execute(
            "INSERT INTO public.clients (legal_name, document_number, document_type)"
            " VALUES ('Cliente Teste', %s, 'cnpj') RETURNING id",
            (uuid.uuid4().hex[:14],),
        )
        cid = cur.fetchone()[0]
    conn.close()
    return str(cid)


def _event(user_id, role="analyst", body=None, path=None, query=None):
    return {
        "requestContext": {
            "authorizer": {"context": {"user_id": user_id, "email": "u@t.c", "role": role}}
        },
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": query or {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


@pytest.fixture()
def client_id():
    return _reset_and_seed_client()


def _make_case(user_id, client_id, role="analyst"):
    resp = cases_h.create_case(
        _event(user_id, role, body={"client_id": client_id, "case_type": "contract_analysis"}),
        None,
    )
    return resp


def test_create_get_list_case(client_id):
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]

    assert cases_h.get_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 200
    listed = _data(cases_h.list_cases(_event(a), None))
    assert len(listed) == 1 and listed[0]["id"] == case_id


def test_case_isolation_and_admin(client_id):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]

    assert len(_data(cases_h.list_cases(_event(b), None))) == 0
    assert cases_h.get_case(_event(b, path={"caseId": case_id}), None)["statusCode"] == 404
    assert len(_data(cases_h.list_cases(_event(b, role="admin"), None))) == 1


def test_update_and_delete_case(client_id):
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]

    assert cases_h.update_case(
        _event(a, path={"caseId": case_id}, body={"status": "in_progress"}), None
    )["statusCode"] == 200
    assert cases_h.delete_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 200
    assert cases_h.get_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 404


def test_update_nonexistent_case_returns_404(client_id):
    a = str(uuid.uuid4())
    resp = cases_h.update_case(
        _event(a, path={"caseId": str(uuid.uuid4())}, body={"status": "closed"}), None
    )
    assert resp["statusCode"] == 404


def test_unauthenticated_is_blocked(client_id):
    assert cases_h.list_cases({"requestContext": {}}, None)["statusCode"] == 401


def test_invalid_case_type_returns_400(client_id):
    a = str(uuid.uuid4())
    resp = cases_h.create_case(
        _event(a, body={"client_id": client_id, "case_type": "invalido"}), None
    )
    assert resp["statusCode"] == 400


def test_case_result_only_for_visible_case(client_id):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]

    ok = cr_h.create_case_result(
        _event(a, body={"case_id": case_id, "result_type": "due_diligence",
                        "findings": {"score": 1}, "risk_level": "low"}),
        None,
    )
    assert ok["statusCode"] == 201

    blocked = cr_h.create_case_result(
        _event(b, body={"case_id": case_id, "result_type": "due_diligence",
                        "findings": {}, "risk_level": "low"}),
        None,
    )
    assert blocked["statusCode"] == 404  # caso não visível ao usuário B
