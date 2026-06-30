"""Fase 3 — testes de integração dos handlers de clients (PG18).

`public.clients` é catálogo COMPARTILHADO (sem RLS, sem created_by): leitura para
qualquer autenticado; escrita só para writer (admin/analyst); viewer só lê.
"""
import json
import uuid

import psycopg2
import pytest

from src.handlers import clients as c

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


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


def _event(role="analyst", body=None, path=None, query=None, org_id=SYSTEM_ORG):
    return {
        "requestContext": {"authorizer": {"user_id": str(uuid.uuid4()),
                                          "email": "u@t.c", "role": role, "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": query or {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


_VALID = {"legal_name": "Empresa XYZ", "document_type": "cnpj",
          "document_number": "11.222.333/0001-81"}  # CNPJ com DV válido


def _create(role="analyst", **over):
    body = {**_VALID, **over}
    return c.create_client(_event(role, body=body), None)


# ── create ──────────────────────────────────────────────────────────────────
def test_create_and_get(clean_clients):
    cid = _data(_create())["id"]
    got = _data(c.get_client(_event(path={"clientId": cid}), None))
    assert got["legal_name"] == "Empresa XYZ"
    assert got["document_number"] == "11222333000181"  # só dígitos (analyst vê completo)


def test_viewer_sees_masked_document(clean_clients):
    cid = _data(_create(email="contato@acme.com", phone="11999998888"))["id"]
    # analyst vê completo
    full = _data(c.get_client(_event(path={"clientId": cid}), None))
    assert full["document_number"] == "11222333000181" and full["email"] == "contato@acme.com"
    # viewer: documento mascarado E sem dados de contato/endereço (LGPD)
    got = _data(c.get_client(_event(role="viewer", path={"clientId": cid}), None))
    assert got["document_number"] == "**********0181"
    assert got["email"] is None and got["phone"] is None and got["address"] is None
    listed = _data(c.list_clients(_event(role="viewer"), None))
    assert listed[0]["document_number"] == "**********0181" and listed[0]["email"] is None


def test_create_invalid_checksum_400(clean_clients):
    # CNPJ com tamanho certo mas dígito verificador inválido
    assert _create(document_number="12.345.678/0001-90")["statusCode"] == 400


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
    # outro usuário (viewer) DA MESMA organização enxerga o mesmo catálogo
    listed = _data(c.list_clients(_event(role="viewer"), None))
    assert len(listed) == 1


def test_clients_isolated_by_org(clean_clients):
    cid = _data(_create())["id"]  # criado na org de sistema
    # usuário de OUTRA organização não vê o cliente (RLS por org)
    assert c.get_client(_event(path={"clientId": cid}, org_id=OTHER_ORG), None)["statusCode"] == 404
    assert len(_data(c.list_clients(_event(org_id=OTHER_ORG), None))) == 0


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


def test_update_status_syncs_is_active(clean_clients):
    cid = _data(_create())["id"]
    c.update_client(_event(path={"clientId": cid}, body={"status": "inactive"}), None)
    assert _row_is_active(cid) is False
    c.update_client(_event(path={"clientId": cid}, body={"status": "active"}), None)
    assert _row_is_active(cid) is True


# ── bordas ──────────────────────────────────────────────────────────────────
def test_list_invalid_pagination_400(clean_clients):
    resp = c.list_clients(_event(query={"page_size": "abc"}), None)
    assert resp["statusCode"] == 400


def test_create_invalid_json_400(clean_clients):
    ev = _event()
    ev["body"] = "{nao-e-json"
    assert c.create_client(ev, None)["statusCode"] == 400


def test_update_no_fields_400(clean_clients):
    cid = _data(_create())["id"]
    assert c.update_client(_event(path={"clientId": cid}, body={}), None)["statusCode"] == 400


def _row_is_active(cid):
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT is_active FROM public.clients WHERE id = %s", (cid,))
        val = cur.fetchone()[0]
    conn.close()
    return val
