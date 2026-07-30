"""Modelo B — abas liberáveis por usuário (ver docs/PERFIS_ACESSO_SPEC.md §8).

Cobre o mecanismo PURO (sem banco): normalização de telas_extra, cálculo das telas
efetivas (base do perfil ∪ extras ∩ liberáveis), o decorator require_tela (fail-closed)
e a leitura de telas_extra em get_user_from_event (string do API Gateway ou lista).
"""
import json
import os
import uuid

import jwt
import pytest

from _dbadmin import admin_conn
from src.handlers import dashboard as dash
from src.handlers import organizations as orgs
from src.handlers import users as u
from src.handlers.users import hash_password
from src.utils.context import (
    LIBERATABLE_TELAS,
    PERFIL_TELAS,
    clean_telas_extra,
    effective_telas,
    get_user_from_event,
    require_tela,
)

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"


def _ok_handler(event, context):
    return {"statusCode": 200}


# ─────────────────────────── clean_telas_extra ──────────────────────────────
def test_clean_filtra_para_liberaveis():
    assert clean_telas_extra(["dashboard", "documentos"]) == {"dashboard", "documentos"}
    # telas não-liberáveis (firma) e desconhecidas são descartadas (fail-safe)
    assert clean_telas_extra(["dashboard", "financeiro", "administracao", "lixo"]) == {"dashboard"}


def test_clean_aceita_string_do_api_gateway():
    # o API Gateway serializa o context como string 'a,b'
    assert clean_telas_extra("dashboard,documentos") == {"dashboard", "documentos"}
    assert clean_telas_extra("dashboard,financeiro") == {"dashboard"}  # financeiro descartado


def test_clean_vazio_ou_none():
    for empty in (None, "", [], ()):
        assert clean_telas_extra(empty) == frozenset()


def test_liberaveis_nunca_incluem_telas_da_firma():
    assert LIBERATABLE_TELAS.isdisjoint({"administracao", "financeiro", "analista", "clientes"})


# ─────────────────────────── effective_telas ────────────────────────────────
def test_effective_soma_base_e_extras():
    eff = effective_telas("cliente_comum", ["dashboard"])
    assert {"dashboard", "novo_pedido", "casos"} <= eff  # base do cliente + a liberada
    # nunca fura telas da firma via extras
    assert eff.isdisjoint({"administracao", "financeiro"})


def test_effective_extra_nao_liberavel_e_ignorado():
    assert "financeiro" not in effective_telas("cliente_comum", ["financeiro"])
    assert effective_telas("cliente_comum", ["financeiro"]) == PERFIL_TELAS["cliente_comum"]


def test_effective_administrador_ja_ve_tudo():
    assert effective_telas("administrador", []) == PERFIL_TELAS["administrador"]
    assert effective_telas("administrador", ["dashboard"]) == PERFIL_TELAS["administrador"]


def test_effective_perfil_ausente_so_extras():
    # perfil None (token legado) + sem extras => nada; não vaza tela da firma
    assert effective_telas(None, None) == frozenset()
    assert effective_telas(None, ["dashboard"]) == {"dashboard"}


# ─────────────────────────── require_tela (fail-closed) ─────────────────────
def _event(perfil, telas_extra):
    return {"user": {"perfil": perfil, "telas_extra": clean_telas_extra(telas_extra)}}


def test_require_tela_bloqueia_sem_liberacao():
    guarded = require_tela("documentos")(_ok_handler)
    assert guarded(_event("cliente_comum", []), None)["statusCode"] == 403


def test_require_tela_libera_com_grant():
    guarded = require_tela("documentos")(_ok_handler)
    assert guarded(_event("cliente_comum", ["documentos"]), None)["statusCode"] == 200


def test_require_tela_administrador_passa_pela_base():
    guarded = require_tela("documentos")(_ok_handler)
    assert guarded(_event("administrador", []), None)["statusCode"] == 200


def test_require_tela_failclosed_sem_user():
    assert require_tela("dashboard")(_ok_handler)({}, None)["statusCode"] == 403


# ─────────────────────────── get_user_from_event lê telas_extra ─────────────
def _authz(perfil, telas_extra):
    return {"requestContext": {"authorizer": {
        "user_id": str(uuid.uuid4()), "role": "viewer",
        "organization_id": SYSTEM_ORG, "perfil": perfil,
        "telas_extra": telas_extra}}}


def test_get_user_parseia_telas_extra_string():
    # string do API Gateway; financeiro descartado por não ser liberável
    user = get_user_from_event(_authz("cliente_comum", "dashboard,financeiro"))
    assert user["telas_extra"] == {"dashboard"}


def test_get_user_sem_telas_extra():
    ev = {"requestContext": {"authorizer": {
        "user_id": str(uuid.uuid4()), "role": "admin",
        "organization_id": SYSTEM_ORG, "perfil": "administrador"}}}
    assert get_user_from_event(ev)["telas_extra"] == frozenset()


# ─────────────── integração: login/me carregam as telas efetivas ─────────────
@pytest.fixture()
def clean_users():
    conn = admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.password_resets, public.users CASCADE")
    conn.close()


def _seed(email, password, perfil, telas_extra):
    uid = str(uuid.uuid4())
    conn = admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status,"
            " organization_id, perfil, telas_extra)"
            " VALUES (%s,%s,%s,'Test','viewer','active',%s,%s,%s)",
            (uid, email, hash_password(password), SYSTEM_ORG, perfil, telas_extra))
    conn.close()
    return uid


def test_login_e_me_carregam_telas_efetivas(clean_users):
    # cliente_comum com "documentos" liberado pelo admin (Modelo B).
    uid = _seed("cli.telas@quorya.com", "SenhaForte#2026", "cliente_comum", ["documentos"])
    resp = u.login({"body": json.dumps(
        {"email": "cli.telas@quorya.com", "password": "SenhaForte#2026"})}, None)
    assert resp["statusCode"] == 200, resp
    data = json.loads(resp["body"])["data"]
    # telas efetivas = base do cliente_comum ∪ a liberada
    assert set(data["user"]["telas"]) == {
        "novo_pedido", "casos", "relatorios", "configuracoes", "documentos"}
    # o token carrega telas_extra p/ o authorizer
    claims = jwt.decode(data["access_token"], os.environ["JWT_SECRET_KEY"],
                        algorithms=["HS256"], options={"verify_exp": False})
    assert claims["telas_extra"] == ["documentos"]
    # /me devolve as mesmas telas efetivas
    me_ev = {"requestContext": {"authorizer": {
        "user_id": uid, "role": "viewer", "organization_id": SYSTEM_ORG,
        "perfil": "cliente_comum", "telas_extra": "documentos"}}}
    me_resp = u.me(me_ev, None)
    assert me_resp["statusCode"] == 200
    assert "documentos" in json.loads(me_resp["body"])["data"]["telas"]


# ─────────── Increment B: signup, gate do dashboard, endpoint de telas ───────
def _authz_admin(perfil="administrador", role="admin"):
    return {"requestContext": {"authorizer": {
        "user_id": str(uuid.uuid4()), "role": role,
        "organization_id": SYSTEM_ORG, "perfil": perfil}}}


def test_signup_publico_nasce_cliente_comum_org_individual(clean_users):
    resp = u.create_user({"body": json.dumps(
        {"email": "novo.cli@quorya.com", "password": "SenhaForte#2026",
         "name": "Novo Cliente"})}, None)
    assert resp["statusCode"] == 201, resp
    data = json.loads(resp["body"])["data"]
    assert data["perfil"] == "cliente_comum"
    conn = admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT perfil, organization_id FROM public.users WHERE id = %s",
                    (data["user_id"],))
        perfil_db, org_id_db = cur.fetchone()
        cur.execute("SELECT type FROM public.organizations WHERE id = %s", (org_id_db,))
        (org_type,) = cur.fetchone()
    conn.close()
    assert perfil_db == "cliente_comum"
    assert org_type == "individual"


def test_dashboard_bloqueia_cliente_comum_sem_a_aba():
    # cliente_comum não tem 'dashboard' na base -> require_tela dá 403 (antes do DB)
    assert dash.get_stats(_authz_admin(perfil="cliente_comum"), None)["statusCode"] == 403


def test_update_telas_rejeita_tela_nao_liberavel():
    ev = _authz_admin()
    ev["pathParameters"] = {"orgId": str(uuid.uuid4()), "userId": str(uuid.uuid4())}
    ev["body"] = json.dumps({"telas": ["financeiro"]})  # não liberável -> 400
    assert orgs.update_org_user_telas(ev, None)["statusCode"] == 400


def test_update_telas_exige_perfil_administrador():
    ev = _authz_admin(perfil="cliente_comum")  # role admin, mas NÃO administrador
    ev["pathParameters"] = {"orgId": str(uuid.uuid4()), "userId": str(uuid.uuid4())}
    ev["body"] = json.dumps({"telas": ["documentos"]})
    assert orgs.update_org_user_telas(ev, None)["statusCode"] == 403
