"""Fase 3 — testes de integração dos handlers de clients (PG18).

`public.clients` é catálogo COMPARTILHADO (sem RLS, sem created_by): leitura para
qualquer autenticado; escrita só para writer (admin/analyst); viewer só lê.
"""
from _dbadmin import admin_conn
import json
import uuid

import psycopg2
import pytest

from src.handlers import clients as c

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _admin_conn():
    return admin_conn()


@pytest.fixture()
def clean_clients():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.cases, public.clients RESTART IDENTITY CASCADE")
    conn.close()


def _seed_user(user_id, role, org):
    """Semeia public.users com o papel/status atuais — as rotas de escrita reconsultam
    o papel ATUAL no banco (assert_active_writer, SEC-01), então um user_id sintético
    não-semeado seria recusado com 403 (janela de revogação)."""
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s,%s,'x','Test',%s,'active',%s)"
            " ON CONFLICT (id) DO UPDATE SET role=EXCLUDED.role, status='active'",
            (user_id, f"u_{user_id}@t.c", role, org))
    conn.close()


def _seed_user_status(user_id, role, status):
    """Ajusta papel/status de um user já existente (para simular revogação)."""
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE public.users SET role=%s, status=%s WHERE id=%s",
                    (role, status, user_id))
    conn.close()


def _event_same(user_id, role, body=None, path=None, org_id=SYSTEM_ORG):
    """Evento com um user_id FIXO (não semeia) — para exercitar o recheck de revogação
    depois que o banco já foi alterado por _seed_user_status."""
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "perfil": "administrador", "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": {},
    }


def _event(role="analyst", body=None, path=None, query=None, org_id=SYSTEM_ORG):
    uid = str(uuid.uuid4())
    # O user do token existe de verdade no banco, com o papel do teste — senão o recheck
    # de escrita (SEC-01) recusaria antes mesmo de exercitar o comportamento sob teste.
    _seed_user(uid, role, org_id)
    return {
        "requestContext": {"authorizer": {"user_id": uid,
                                          "email": "u@t.c", "role": role, "perfil": "administrador", "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": query or {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    d = json.loads(resp["body"])["data"]
    # listagem paginada retorna envelope {items,total,...}; testes tratam como lista
    if isinstance(d, dict) and "items" in d:
        return d["items"]
    return d


_VALID = {"legal_name": "Empresa XYZ", "document_type": "cnpj",
          "document_number": "11.222.333/0001-81"}  # CNPJ com DV válido


def _create(role="analyst", **over):
    body = {**_VALID, **over}
    return c.create_client(_event(role, body=body), None)


def test_list_clients_busca_q(clean_clients):
    # #27: busca textual (?q=) por nome do cliente
    _create(legal_name="Zebra Advocacia")
    achados = _data(c.list_clients(_event(query={"q": "zebra"}), None))
    assert any(x["name"] == "Zebra Advocacia" for x in achados)
    # termo sem correspondência -> vazio (sem falso-positivo)
    assert _data(c.list_clients(_event(query={"q": "qqqnaoexiste"}), None)) == []


# ── create ──────────────────────────────────────────────────────────────────
def test_create_client_shape_v2_do_frontend(clean_clients):
    # frontend envia name/cpf/person_type/contract_role/address (shape V2) — o backend
    # mapeia name->legal_name, cpf->document_number e ignora os extras.
    resp = c.create_client(_event(body={
        "name": "Maria Teste", "cpf": "060.380.601-54", "document_type": "cpf",
        "person_type": "individual", "contract_role": "contratada",
        "address": "Rua X, 10 - Centro", "rg": "12.345.678-9"}), None)
    assert resp["statusCode"] == 201, resp
    data = json.loads(resp["body"])["data"]
    assert data["name"] == "Maria Teste" and data["legal_name"] == "Maria Teste"
    assert data["document_number"] == "06038060154"  # analyst vê completo


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
    # viewer: documento mascarado (util pii.mask_document — 2 últimos dígitos, formato
    # CNPJ) E sem dados de contato/endereço (LGPD)
    got = _data(c.get_client(_event(role="viewer", path={"clientId": cid}), None))
    assert got["document_number"] == "**.***.***/****-81"
    assert got["email"] is None and got["phone"] is None and got["address"] is None
    listed = _data(c.list_clients(_event(role="viewer"), None))
    assert listed[0]["document_number"] == "**.***.***/****-81" and listed[0]["email"] is None


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


def test_escritor_revogado_no_banco_bloqueado(clean_clients):
    """SEC-01: fecha a janela de revogação (~2h) do JWT nas rotas de escrita.

    O token continua dizendo `analyst` (papel de escrita válido por ~2h), mas o banco
    já rebaixou/desativou a conta. As mutações têm de recusar com 403 — antes da
    correção, o create/update/delete gravava normalmente até o token expirar.
    """
    ev = _event(role="analyst")  # cria e semeia um analyst ATIVO
    uid = ev["requestContext"]["authorizer"]["user_id"]
    # cria um cliente legítimo enquanto ainda é writer ativo
    cid = _data(c.create_client({**ev, "body": json.dumps(_VALID)}, None))["id"]

    # (a) rebaixado a viewer no banco, mas o TOKEN ainda diz analyst
    _seed_user_status(uid, "viewer", "active")
    assert c.update_client(_event_same(uid, "analyst", body={"legal_name": "Alterado"},
                                       path={"clientId": cid}), None)["statusCode"] == 403
    assert c.delete_client(_event_same(uid, "analyst", path={"clientId": cid}),
                           None)["statusCode"] == 403

    # (b) conta desativada (papel ainda analyst) — usa um create com CNPJ válido para
    # garantir que o 403 vem do recheck de revogação, e não de um 400 de validação.
    _seed_user_status(uid, "analyst", "inactive")
    assert c.create_client(_event_same(uid, "analyst", body=_VALID),
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
