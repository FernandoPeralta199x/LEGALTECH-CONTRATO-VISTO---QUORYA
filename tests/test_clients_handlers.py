"""Fase 3 — testes de integração dos handlers de clients (PG18).

`public.clients` é catálogo COMPARTILHADO (sem RLS, sem created_by): leitura para
qualquer autenticado; escrita só para writer (admin/analyst); viewer só lê.
"""
import json
import uuid

import psycopg2
import pytest

from src.handlers import clients as c


def _admin_conn():
    return psycopg2.connect(
        host="localhost", port=5433, user="dbadmin",
        password="localdev_cv", dbname="contrato_visto", connect_timeout=5,
    )


@pytest.fixture()
def clean_clients():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.cases, public.clients RESTART IDENTITY CASCADE")
    conn.close()


def _event(role="analyst", body=None, path=None, query=None):
    return {
        "requestContext": {"authorizer": {"user_id": str(uuid.uuid4()),
                                          "email": "u@t.c", "role": role}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": query or {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


_VALID = {"legal_name": "Empresa XYZ", "document_type": "cnpj",
          "document_number": "12.345.678/0001-90"}


def _create(role="analyst", **over):
    body = {**_VALID, **over}
    return c.create_client(_event(role, body=body), None)


# ── create ──────────────────────────────────────────────────────────────────
def test_create_and_get(clean_clients):
    cid = _data(_create())["id"]
    got = _data(c.get_client(_event(path={"clientId": cid}), None))
    assert got["legal_name"] == "Empresa XYZ"
    assert got["document_number"] == "12345678000190"  # só dígitos


def test_create_cpf_coherence(clean_clients):
    ok = _create(document_type="cpf", document_number="529.982.247-25")
    assert ok["statusCode"] == 201
    bad = _create(document_type="cpf", document_number="12.345.678/0001-90")  # 14 díg p/ cpf
    assert bad["statusCode"] == 400


def test_create_duplicate_document_409(clean_clients):
    assert _create()["statusCode"] == 201
    assert _create(legal_name="Outra")["statusCode"] == 409  # mesmo document_number


def test_viewer_cannot_create(clean_clients):
    assert _create(role="viewer")["statusCode"] == 403


def test_unauthenticated_blocked(clean_clients):
    assert c.list_clients({"requestContext": {}}, None)["statusCode"] == 401


# ── read (compartilhado) ────────────────────────────────────────────────────
def test_list_is_shared_across_users(clean_clients):
    _create()
    # outro usuário (viewer) enxerga o mesmo catálogo
    listed = _data(c.list_clients(_event(role="viewer"), None))
    assert len(listed) == 1


def test_get_nonexistent_404(clean_clients):
    assert c.get_client(_event(path={"clientId": str(uuid.uuid4())}), None)["statusCode"] == 404


# ── update / delete ─────────────────────────────────────────────────────────
def test_update_fields(clean_clients):
    cid = _data(_create())["id"]
    resp = c.update_client(_event(path={"clientId": cid},
                                  body={"legal_name": "Novo Nome", "address_state": "SP"}), None)
    assert resp["statusCode"] == 200
    got = _data(c.get_client(_event(path={"clientId": cid}), None))
    assert got["legal_name"] == "Novo Nome" and got["address"]["state"] == "SP"


def test_viewer_cannot_update(clean_clients):
    cid = _data(_create())["id"]
    resp = c.update_client(_event(role="viewer", path={"clientId": cid},
                                  body={"legal_name": "X Nome"}), None)
    assert resp["statusCode"] == 403


def test_soft_delete_removes_from_list(clean_clients):
    cid = _data(_create())["id"]
    assert c.delete_client(_event(role="admin", path={"clientId": cid}), None)["statusCode"] == 200
    # soft delete: some da listagem (status inactive), mas get ainda acha
    assert len(_data(c.list_clients(_event(), None))) == 0
    assert c.get_client(_event(path={"clientId": cid}), None)["statusCode"] == 200


def test_viewer_cannot_delete(clean_clients):
    cid = _data(_create())["id"]
    assert c.delete_client(_event(role="viewer", path={"clientId": cid}),
                           None)["statusCode"] == 403


def test_update_nonexistent_404(clean_clients):
    resp = c.update_client(_event(path={"clientId": str(uuid.uuid4())},
                                  body={"legal_name": "Nome"}), None)
    assert resp["statusCode"] == 404
