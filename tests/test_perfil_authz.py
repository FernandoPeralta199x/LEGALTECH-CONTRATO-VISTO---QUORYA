"""Fase 1a — mecanismo de PERFIS de acesso (ver docs/PERFIS_ACESSO_SPEC.md).

Cobre: propagação do claim `perfil` (login -> token -> /me), leitura em
get_user_from_event (ausente/inválido => None, fail-safe), o decorator
require_perfil (fail-closed) e o mapa PERFIL_TELAS (fonte da verdade das telas).
"""
from _dbadmin import admin_conn
import json
import os
import uuid

import jwt
import pytest

from src.handlers import users as u
from src.handlers.users import hash_password
from src.utils.context import (PERFIL_TELAS, VALID_PERFIS, get_user_from_event,
                               require_perfil)

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"


# ─────────────────────────── helpers ────────────────────────────────────────
def _authz_event(perfil, role="admin", org=SYSTEM_ORG, uid=None):
    """Evento com o contexto do authorizer (claims achatados), incluindo `perfil`."""
    return {"requestContext": {"authorizer": {
        "user_id": uid or str(uuid.uuid4()), "role": role,
        "organization_id": org, "perfil": perfil}}}


def _ok_handler(event, context):
    return {"statusCode": 200}


def _pub(body):
    return {"body": json.dumps(body)}


@pytest.fixture()
def clean_users():
    conn = admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.password_resets, public.users CASCADE")
    conn.close()


def _seed_user(email, password, perfil, role="admin", org=SYSTEM_ORG):
    uid = str(uuid.uuid4())
    conn = admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users"
            " (id, email, password_hash, name, role, status, organization_id, perfil)"
            " VALUES (%s,%s,%s,'Test',%s,'active',%s,%s)",
            (uid, email, hash_password(password), role, org, perfil))
    conn.close()
    return uid


# ─────────────────────────── require_perfil (fail-closed) ────────────────────
def test_require_perfil_permite_o_perfil_listado():
    ev = _authz_event("administrador")
    ev["user"] = get_user_from_event(ev)
    assert require_perfil("administrador")(_ok_handler)(ev, None)["statusCode"] == 200


def test_require_perfil_nega_perfil_diferente():
    ev = _authz_event("empresarial")
    ev["user"] = get_user_from_event(ev)
    assert require_perfil("administrador")(_ok_handler)(ev, None)["statusCode"] == 403


def test_require_perfil_failclosed_quando_perfil_ausente():
    # Token legado sem `perfil` -> None -> negado (nunca fail-open).
    ev = {"requestContext": {"authorizer": {
        "user_id": str(uuid.uuid4()), "role": "admin", "organization_id": SYSTEM_ORG}}}
    ev["user"] = get_user_from_event(ev)
    assert require_perfil("administrador")(_ok_handler)(ev, None)["statusCode"] == 403


def test_require_perfil_aceita_lista_de_perfis():
    guarded = require_perfil("empresarial", "cliente_comum")(_ok_handler)
    for p in ("empresarial", "cliente_comum"):
        ev = _authz_event(p); ev["user"] = get_user_from_event(ev)
        assert guarded(ev, None)["statusCode"] == 200
    ev = _authz_event("administrador"); ev["user"] = get_user_from_event(ev)
    assert guarded(ev, None)["statusCode"] == 403


# ─────────────────────────── get_user_from_event lê o perfil ────────────────
def test_get_user_le_perfil_valido():
    assert get_user_from_event(_authz_event("empresarial"))["perfil"] == "empresarial"


def test_get_user_perfil_invalido_vira_none():
    assert get_user_from_event(_authz_event("root"))["perfil"] is None  # fora do vocabulário


def test_get_user_sem_perfil_nao_regride_campos():
    ev = {"requestContext": {"authorizer": {
        "user_id": str(uuid.uuid4()), "role": "analyst", "organization_id": SYSTEM_ORG}}}
    user = get_user_from_event(ev)
    assert user["perfil"] is None and user["role"] == "analyst"


# ─────────────────────────── mapa PERFIL_TELAS ──────────────────────────────
def test_mapa_cobre_todos_os_perfis():
    assert set(PERFIL_TELAS.keys()) == VALID_PERFIS


def test_administrador_ve_admin_e_financeiro():
    assert {"administracao", "financeiro"} <= PERFIL_TELAS["administrador"]


def test_empresarial_sem_admin_financeiro_analista_clientes():
    emp = PERFIL_TELAS["empresarial"]
    assert emp.isdisjoint({"administracao", "financeiro", "analista", "clientes"})
    assert {"dashboard", "novo_pedido", "casos", "documentos", "relatorios", "configuracoes"} <= emp


def test_cliente_comum_e_minimo():
    assert PERFIL_TELAS["cliente_comum"] == frozenset(
        {"novo_pedido", "casos", "relatorios", "configuracoes"})


# ─────────────────────────── integração: login/me carregam o perfil ─────────
def test_login_token_e_me_carregam_perfil(clean_users):
    uid = _seed_user("emp.perfil@quorya.com", "SenhaForte#2026", perfil="empresarial")
    resp = u.login(_pub({"email": "emp.perfil@quorya.com", "password": "SenhaForte#2026"}), None)
    assert resp["statusCode"] == 200, resp
    data = json.loads(resp["body"])["data"]
    assert data["user"]["perfil"] == "empresarial"
    claims = jwt.decode(data["access_token"], os.environ["JWT_SECRET_KEY"],
                        algorithms=["HS256"], options={"verify_exp": False})
    assert claims["perfil"] == "empresarial"

    me_ev = {"requestContext": {"authorizer": {
        "user_id": uid, "role": "admin", "organization_id": SYSTEM_ORG,
        "perfil": claims["perfil"]}}}
    me_resp = u.me(me_ev, None)
    assert me_resp["statusCode"] == 200
    assert json.loads(me_resp["body"])["data"]["perfil"] == "empresarial"


# ─────────────────────────── enforcement (Fase 1b) ──────────────────────────
def test_financeiro_bloqueia_empresarial_mesmo_com_role_admin():
    """O CENÁRIO de segurança: um usuário externo pode ser 'admin' da PRÓPRIA org.
    require_role("admin") sozinho o deixaria entrar no Financeiro — o perfil é que
    separa firma de cliente. Deve dar 403 ANTES do corpo do handler (não vaza dado)."""
    from src.handlers import financial_receivables as rec_h
    ev = {"requestContext": {"authorizer": {
        "user_id": str(uuid.uuid4()), "role": "admin",           # admin da própria org
        "organization_id": SYSTEM_ORG, "perfil": "empresarial"}},  # mas NÃO é a firma
        "queryStringParameters": {"period": "year"}}
    assert rec_h.list_receivables(ev, None)["statusCode"] == 403


def test_administracao_bloqueia_cliente_comum():
    """Cliente comum não acessa Administração (gestão de usuários)."""
    ev = {"requestContext": {"authorizer": {
        "user_id": str(uuid.uuid4()), "role": "admin",
        "organization_id": SYSTEM_ORG, "perfil": "cliente_comum"}},
        "queryStringParameters": {}}
    assert u.list_users(ev, None)["statusCode"] == 403
