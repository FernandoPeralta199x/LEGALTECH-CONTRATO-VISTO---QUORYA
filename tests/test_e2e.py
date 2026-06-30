"""Fase E2E — teste de PONTA A PONTA de todo o programa (PG18).

Diferente dos testes por fase (que injetam o contexto do authorizer), este exercita
a CADEIA REAL: login → jwt_authorizer.authorize() → handlers de negócio, com tokens
JWT verdadeiros. Cobre o fluxo completo login→authorizer→clients→cases→
case_results→documents→search e os cortes transversais de RLS/RBAC/erro.

Multi-tenant (Fundação V2): membros da operação são semeados na MESMA organização
(o signup público passa a criar uma organização própria — testado em
test_users_handlers). A RLS de cases/case_results/documents ainda é por-dono nesta
fase (Parte 1A); a virada para por-organização é a Parte 1B.
"""
import json
import uuid

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

from src.authorizers import jwt_authorizer
from src.handlers import case_results as cr_h
from src.handlers import cases as cases_h
from src.handlers import clients as clients_h
from src.handlers import documents as docs_h
from src.handlers import search as search_h
from src.handlers import users as users_h
from src.services import rag
from src.services.embeddings import embeddings_service

ORG_ID = "00000000-0000-0000-0000-000000000001"  # org de sistema (migração 005)


def _admin_conn():
    return psycopg2.connect(
        host="localhost", port=5433, user="dbadmin",
        password="localdev_cv", dbname="contrato_visto", connect_timeout=5,
    )


@pytest.fixture()
def clean_db():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.document_embeddings, public.document_chunks,"
            " public.documents, public.case_results, public.cases,"
            " public.clients, public.password_resets, public.users"
            " RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE audit.audit_log RESTART IDENTITY")
    conn.close()


# ── helpers da cadeia real ───────────────────────────────────────────────────
def _seed_user(email, password, role="viewer", org_id=ORG_ID):
    """Cria um usuário diretamente numa organização (membros da mesma operação)."""
    uid = str(uuid.uuid4())
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users"
            " (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s, %s, %s, 'User', %s, 'active', %s)",
            (uid, email, users_h.hash_password(password), role, org_id))
    conn.close()
    return uid


def _seed_admin(email="admin@acme.com", password="Admin1234", org_id=ORG_ID):
    return _seed_user(email, password, role="admin", org_id=org_id)


def _login(email, password):
    resp = users_h.login({"body": json.dumps({"email": email, "password": password})}, None)
    assert resp["statusCode"] == 200, resp
    return json.loads(resp["body"])["data"]["token"]


def _authctx(token):
    """Passa o token pelo JWT Authorizer real e devolve o context (achatado)."""
    ev = {"type": "TOKEN", "authorizationToken": f"Bearer {token}",
          "methodArn": "arn:aws:execute-api:sa-east-1:123:api/dev/GET/x"}
    res = jwt_authorizer.authorize(ev, None)
    assert res["policyDocument"]["Statement"][0]["Effect"] == "Allow", res
    return res["context"]


def _ev(ctx, body=None, path=None, query=None):
    return {
        "requestContext": {"authorizer": ctx},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": query or {},
    }


def _ok(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"]).get("data")


# ── fluxo feliz, ponta a ponta ───────────────────────────────────────────────
def test_full_flow(clean_db):
    # 1) bootstrap admin + login real + authorizer
    _seed_admin()
    ctx_admin = _authctx(_login("admin@acme.com", "Admin1234"))
    assert ctx_admin["role"] == "admin"
    assert ctx_admin["organization_id"] == ORG_ID

    # 2) membro viewer da MESMA organização + admin promove a analyst
    ana_id = _seed_user("ana@acme.com", "Senha1234", role="viewer")
    listed = _ok(users_h.list_users(_ev(ctx_admin), None))
    assert any(u["id"] == ana_id for u in listed)  # admin enxerga membro da sua org
    assert _ok(users_h.get_user(_ev(ctx_admin, path={"userId": ana_id}), None))["role"] == "viewer"
    assert users_h.update_user(
        _ev(ctx_admin, path={"userId": ana_id}, body={"role": "analyst"}), None)["statusCode"] == 200

    # 3) ana faz login real (já analyst) e passa pelo authorizer
    ctx_ana = _authctx(_login("ana@acme.com", "Senha1234"))
    assert ctx_ana["role"] == "analyst" and ctx_ana["user_id"] == ana_id

    # 4) admin cria client (catálogo compartilhado)
    client_id = _ok(clients_h.create_client(_ev(ctx_admin, body={
        "legal_name": "ACME Ltda", "document_type": "cnpj",
        "document_number": "11.222.333/0001-81"}), None))["id"]

    # 5) ana cria case -> case_result -> document
    case_id = _ok(cases_h.create_case(_ev(ctx_ana, body={
        "client_id": client_id, "case_type": "contract_analysis"}), None))["id"]
    assert cr_h.create_case_result(_ev(ctx_ana, body={
        "case_id": case_id, "result_type": "due_diligence",
        "findings": {"score": 1}, "risk_level": "low"}), None)["statusCode"] == 201
    doc_id = _ok(docs_h.upload_document(_ev(ctx_ana, body={
        "case_id": case_id, "file_name": "contrato.pdf", "file_type": "pdf"}), None))["document_id"]

    # 6) ingestão de embeddings (admin/backoffice) + busca semântica da ana
    _seed_embeddings(doc_id, ["cláusula de rescisão", "objeto do contrato"])
    results = _ok(search_h.search_clauses(_ev(ctx_ana, body={
        "case_id": case_id, "query": "cláusula de rescisão"}), None))
    assert results[0]["segment_text"] == "cláusula de rescisão"

    # 7) ana lê o próprio document
    assert docs_h.get_document(_ev(ctx_ana, path={"docId": doc_id}), None)["statusCode"] == 200


# ── cortes transversais: RBAC + RLS + token inválido ─────────────────────────
def test_rbac_and_rls_across_users(clean_db):
    _seed_admin()
    ctx_admin = _authctx(_login("admin@acme.com", "Admin1234"))
    client_id = _ok(clients_h.create_client(_ev(ctx_admin, body={
        "legal_name": "ACME", "document_type": "cnpj",
        "document_number": "11.222.333/0001-81"}), None))["id"]
    case_id = _ok(cases_h.create_case(_ev(ctx_admin, body={
        "client_id": client_id, "case_type": "contract_analysis"}), None))["id"]

    # viewer (mesma org) não escreve e não vê case alheio (RLS por-dono nesta fase)
    _seed_user("bob@acme.com", "Senha1234", role="viewer")
    ctx_bob = _authctx(_login("bob@acme.com", "Senha1234"))
    assert ctx_bob["role"] == "viewer"
    assert clients_h.create_client(_ev(ctx_bob, body={
        "legal_name": "X", "document_type": "cpf",
        "document_number": "529.982.247-25"}), None)["statusCode"] == 403
    assert cases_h.create_case(_ev(ctx_bob, body={
        "client_id": client_id, "case_type": "contract_analysis"}), None)["statusCode"] == 403
    assert cases_h.get_case(_ev(ctx_bob, path={"caseId": case_id}), None)["statusCode"] == 404
    assert users_h.list_users(_ev(ctx_bob), None)["statusCode"] == 403  # admin-only


def test_invalid_token_denied(clean_db):
    res = jwt_authorizer.authorize(
        {"type": "TOKEN", "authorizationToken": "Bearer nao-e-um-jwt",
         "methodArn": "arn:aws:execute-api:sa-east-1:123:api/dev/GET/x"}, None)
    assert res["policyDocument"]["Statement"][0]["Effect"] == "Deny"


def test_login_wrong_password_no_token(clean_db):
    _seed_admin()
    assert users_h.login(
        {"body": json.dumps({"email": "admin@acme.com", "password": "errada"})},
        None)["statusCode"] == 401


# ── util ─────────────────────────────────────────────────────────────────────
def _seed_embeddings(doc_id, segments):
    conn = _admin_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=RealDictCursor)
    for i, text in enumerate(segments):
        chunk_id = rag.store_chunk(cur, doc_id, i, text)
        rag.store_embedding(cur, doc_id, chunk_id, text, embeddings_service.embed(text))
    cur.close()
    conn.close()
