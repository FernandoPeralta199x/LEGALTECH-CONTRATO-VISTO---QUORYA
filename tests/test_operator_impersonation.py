"""Fase 2 — "operar como org": leitura cross-tenant AUDITADA (ver PERFIS_ACESSO_SPEC §5).

Exercita os handlers reais (organizations.list_client_orgs / list_org_cases) com o
contexto do authorizer injetado, contra o banco de teste (RLS ativa: os handlers
conectam como cv_app). Cobre:
  - listagem das orgs-cliente (só a firma vê; operador NÃO aparece na lista);
  - impersonação: a firma vê os casos do ALVO, e SÓ do alvo (isolamento RLS);
  - AUTORIDADE vem do BANCO, não do token (claim mentiroso não impersona);
  - alvo inválido (não-cliente/inexistente) => 404 e SEM auditoria (rollback);
  - toda impersonação bem-sucedida grava OPERATOR_IMPERSONATION na trilha do alvo.
"""
from _dbadmin import admin_conn
import uuid

import psycopg2
import pytest

from src.handlers import organizations as org_h

OPERADOR_ORG = "00000000-0000-0000-0000-000000000001"   # org de sistema (type='operador')
ISOLATION_ORG = "00000000-0000-0000-0000-0000000000ff"  # org-base de isolamento (operador)


# ─────────────────────────── fixtures / seeds ───────────────────────────────
@pytest.fixture()
def clean_db():
    conn = admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.document_embeddings, public.document_chunks,"
            " public.documents, public.case_results, public.cases,"
            " public.clients, public.password_resets, public.users"
            " RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE audit.audit_log RESTART IDENTITY")
        # Limpa SÓ as orgs-CLIENTE (empresarial/individual), criadas exclusivamente por
        # estes testes — as orgs-base compartilhadas (...0001, ...00ff) são `operador` e
        # ficam intactas (o invariante "nenhum teste trunca organizations" é respeitado).
        cur.execute("DELETE FROM public.organizations"
                    " WHERE type IN ('empresarial','individual')")
    conn.close()


def _c():
    conn = admin_conn(); conn.autocommit = True
    return conn


def _seed_org(name, type_, document=None, document_type=None):
    oid = str(uuid.uuid4())
    conn = _c()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.organizations (id, name, type, document, document_type)"
            " VALUES (%s, %s, %s, %s, %s)", (oid, name, type_, document, document_type))
    conn.close()
    return oid


def _seed_user(org_id, role="admin", perfil="administrador", status="active"):
    uid = str(uuid.uuid4())
    conn = _c()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status,"
            " organization_id, perfil) VALUES (%s, %s, 'x', 'U', %s, %s, %s, %s)",
            (uid, f"{uid}@quorya.com", role, status, org_id, perfil))
    conn.close()
    return uid


def _seed_case(org_id, title="Caso", created_by=None):
    cid_client = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    conn = _c()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.clients (id, legal_name, document_number, organization_id,"
            " status) VALUES (%s, %s, %s, %s, 'active')",
            (cid_client, f"Cliente {title}", "11.222.333/0001-81", org_id))
        cur.execute(
            "INSERT INTO public.cases (id, organization_id, client_id, case_type, status,"
            " priority, created_by, title, code, source_mode)"
            " VALUES (%s, %s, %s, 'contract_analysis', 'open', 'medium', %s, %s, %s, 'local')",
            (case_id, org_id, cid_client, created_by, title, f"CV-{title}"))
    conn.close()
    return case_id


def _event(user_id, role, org_id, perfil, path=None, query=None):
    return {"requestContext": {"authorizer": {
        "user_id": user_id, "role": role, "organization_id": org_id, "perfil": perfil}},
        "pathParameters": path or {}, "queryStringParameters": query or {}}


def _audit_rows(action="OPERATOR_IMPERSONATION"):
    conn = admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, organization_id, resource_type, resource_id,"
                    " api_endpoint FROM audit.audit_log WHERE action = %s", (action,))
        rows = cur.fetchall()
    conn.close()
    return rows


def _body(resp):
    import json
    return json.loads(resp["body"])["data"]


# ─────────────────────────── listagem de orgs-cliente ───────────────────────
def test_lista_orgs_cliente_para_a_firma(clean_db):
    operator = _seed_user(OPERADOR_ORG)
    org_a = _seed_org("ACME Ltda", "empresarial", "11222333000181", "CNPJ")
    _seed_org("Fulano PF", "individual", "52998224725", "CPF")

    resp = org_h.list_client_orgs(_event(operator, "admin", OPERADOR_ORG, "administrador"), None)
    assert resp["statusCode"] == 200, resp
    data = _body(resp)
    ids = {o["id"] for o in data["items"]}
    assert org_a in ids and len(data["items"]) == 2           # as duas orgs-cliente
    assert OPERADOR_ORG not in ids                            # a firma NÃO se lista
    acme = next(o for o in data["items"] if o["id"] == org_a)
    assert acme["document_masked"].endswith("81")            # CNPJ mascarado (LGPD)
    assert "document" not in acme                            # doc cru nunca sai


def test_lista_orgs_cliente_nega_perfil_nao_administrador(clean_db):
    # perfil empresarial nem chega ao banco: barrado pelo require_perfil (decorator).
    u = _seed_user(OPERADOR_ORG, perfil="empresarial")
    resp = org_h.list_client_orgs(_event(u, "admin", OPERADOR_ORG, "empresarial"), None)
    assert resp["statusCode"] == 403


# ─────────────────────────── impersonação: leitura + auditoria ──────────────
def test_operador_ve_casos_do_alvo_e_audita(clean_db):
    operator = _seed_user(OPERADOR_ORG)
    org_a = _seed_org("ACME", "empresarial", "11222333000181", "CNPJ")
    _seed_case(org_a, "A1")

    resp = org_h.list_org_cases(
        _event(operator, "admin", OPERADOR_ORG, "administrador", path={"orgId": org_a}), None)
    assert resp["statusCode"] == 200, resp
    data = _body(resp)
    assert data["total"] == 1 and data["items"][0]["title"] == "A1"

    rows = _audit_rows()
    assert len(rows) == 1
    user_id, org_id, rtype, rid, endpoint = rows[0]
    assert str(user_id) == operator                # ator = operador
    assert str(org_id) == org_a                    # trilha da ORG-ALVO (transparência LGPD)
    assert rtype == "organization" and str(rid) == org_a
    assert endpoint == f"GET /organizations/{org_a}/cases"


def test_impersonacao_isola_um_cliente_do_outro(clean_db):
    operator = _seed_user(OPERADOR_ORG)
    org_a = _seed_org("ACME", "empresarial", "11222333000181", "CNPJ")
    org_b = _seed_org("Outra", "empresarial", "99888777000166", "CNPJ")
    _seed_case(org_a, "DoA")
    _seed_case(org_b, "DoB")

    resp = org_h.list_org_cases(
        _event(operator, "admin", OPERADOR_ORG, "administrador", path={"orgId": org_a}), None)
    data = _body(resp)
    titles = {c["title"] for c in data["items"]}
    assert titles == {"DoA"}                        # NÃO vaza o caso da org B (RLS)


# ─────────────────────────── autoridade vem do BANCO, não do token ──────────
def test_claim_mentiroso_nao_impersona(clean_db):
    """Cenário de segurança: um admin de uma org EMPRESARIAL forja o token dizendo
    perfil=administrador. Os decorators passam, mas o gate do banco (assert_operator_
    admin) descobre que a org dele não é `operador` -> 403 e SEM auditoria."""
    org_a = _seed_org("ACME", "empresarial", "11222333000181", "CNPJ")
    intruder = _seed_user(org_a, role="admin", perfil="empresarial")  # admin da PRÓPRIA org
    _seed_case(org_a, "A1")

    resp = org_h.list_org_cases(
        _event(intruder, "admin", org_a, "administrador",  # <- claim mentiroso
               path={"orgId": org_a}), None)
    assert resp["statusCode"] == 403
    assert _audit_rows() == []                      # nada acessado, nada auditado


# ─────────────────────────── validação do alvo ─────────────────────────────
def test_alvo_nao_cliente_404_sem_auditoria(clean_db):
    operator = _seed_user(OPERADOR_ORG)
    # ISOLATION_ORG é uma org `operador` (não-cliente) já existente — operar como ela
    # deve ser recusado.
    resp = org_h.list_org_cases(
        _event(operator, "admin", OPERADOR_ORG, "administrador",
               path={"orgId": ISOLATION_ORG}), None)
    assert resp["statusCode"] == 404
    assert _audit_rows() == []                      # alvo inválido -> rollback, sem trilha


def test_alvo_inexistente_404(clean_db):
    operator = _seed_user(OPERADOR_ORG)
    resp = org_h.list_org_cases(
        _event(operator, "admin", OPERADOR_ORG, "administrador",
               path={"orgId": str(uuid.uuid4())}), None)
    assert resp["statusCode"] == 404


def test_org_cases_nega_perfil_nao_administrador(clean_db):
    org_a = _seed_org("ACME", "empresarial", "11222333000181", "CNPJ")
    u = _seed_user(OPERADOR_ORG, perfil="cliente_comum")
    resp = org_h.list_org_cases(
        _event(u, "admin", OPERADOR_ORG, "cliente_comum", path={"orgId": org_a}), None)
    assert resp["statusCode"] == 403


def test_orgid_invalido_400(clean_db):
    operator = _seed_user(OPERADOR_ORG)
    resp = org_h.list_org_cases(
        _event(operator, "admin", OPERADOR_ORG, "administrador", path={"orgId": "nao-uuid"}), None)
    assert resp["statusCode"] == 400
