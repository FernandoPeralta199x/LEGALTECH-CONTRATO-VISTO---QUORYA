"""Fase A — testes de integração dos handlers de users/auth (PG18).

`public.users` não tem RLS; valida signup (sempre viewer), login (JWT), RBAC
(viewer/admin), update/delete e o fluxo forgot→reset. Handlers protegidos são
invocados com o `event` que o API Gateway entrega (claims achatados).
"""
from _dbadmin import admin_conn
import json
import os
import uuid

import jwt
import psycopg2
import pytest

from src.handlers import users as u
from src.handlers.users import hash_password

SYSTEM_ORG_ID = "00000000-0000-0000-0000-000000000001"


def _admin_conn():
    return admin_conn()


@pytest.fixture()
def clean_users():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.password_resets, public.users CASCADE")
    conn.close()


def _seed_user(email, password, role="viewer", status="active"):
    uid = str(uuid.uuid4())
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users"
            " (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (uid, email, hash_password(password), "Test", role, status, SYSTEM_ORG_ID),
        )
    conn.close()
    return uid


def _event(user_id, role="viewer", body=None, path=None, org_id=SYSTEM_ORG_ID):
    return {
        "requestContext": {"authorizer": {
            "user_id": user_id, "email": "u@t.c", "role": role, "perfil": "administrador", "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": {},
    }


def _pub(body):
    return {"body": json.dumps(body)}


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"]).get("data")


# ── signup ────────────────────────────────────────────────────────────────
def test_signup_creates_org_and_admin(clean_users):
    resp = u.create_user(_pub({"email": "a@b.c", "password": "SenhaForte#2026", "name": "Ana"}), None)
    assert resp["statusCode"] == 201
    data = _data(resp)
    assert data["role"] == "admin"
    assert uuid.UUID(data["organization_id"])  # signup criou uma organização


def test_signup_ignores_role_field(clean_users):
    # `role` não é aceito no schema (extra=forbid) → 400, sem virar admin.
    resp = u.create_user(
        _pub({"email": "a@b.c", "password": "SenhaForte#2026", "name": "Ana", "role": "admin"}), None
    )
    assert resp["statusCode"] == 400


def test_signup_duplicate_email_409(clean_users):
    u.create_user(_pub({"email": "a@b.c", "password": "SenhaForte#2026", "name": "Ana"}), None)
    resp = u.create_user(_pub({"email": "a@b.c", "password": "SenhaForte#2026", "name": "Bia"}), None)
    assert resp["statusCode"] == 409


def test_signup_weak_password_400(clean_users):
    resp = u.create_user(_pub({"email": "a@b.c", "password": "fraca", "name": "Ana"}), None)
    assert resp["statusCode"] == 400


def test_signup_password_over_72_bytes_400(clean_users):
    # bcrypt rejeita > 72 bytes -> deve dar 400 (não 500)
    longpw = "A1" + "x" * 100
    resp = u.create_user(_pub({"email": "a@b.c", "password": longpw, "name": "Ana"}), None)
    assert resp["statusCode"] == 400


# ── login ─────────────────────────────────────────────────────────────────
def test_login_returns_valid_jwt(clean_users):
    _seed_user("a@b.c", "SenhaForte#2026", role="analyst")
    resp = u.login(_pub({"email": "a@b.c", "password": "SenhaForte#2026"}), None)
    data = _data(resp)
    token = data["access_token"]
    assert data["token_type"] == "bearer" and data["expires_in"] > 0
    assert data["user"]["email"] == "a@b.c" and data["user"]["role"] == "analyst"
    claims = jwt.decode(token, os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
    assert claims["role"] == "analyst" and "exp" in claims
    # claims exigidos pela sessão local do frontend
    assert claims["token_use"] == "dev" and claims["sub"]


def test_login_wrong_password_401(clean_users):
    _seed_user("a@b.c", "SenhaForte#2026")
    assert u.login(_pub({"email": "a@b.c", "password": "Errada123"}), None)["statusCode"] == 401


def test_login_inactive_user_401(clean_users):
    _seed_user("a@b.c", "SenhaForte#2026", status="inactive")
    assert u.login(_pub({"email": "a@b.c", "password": "SenhaForte#2026"}), None)["statusCode"] == 401


def test_login_unknown_email_401(clean_users):
    # usuário inexistente: ainda 401 (e executa bcrypt dummy p/ tempo uniforme)
    assert u.login(_pub({"email": "x@y.z", "password": "SenhaForte#2026"}), None)["statusCode"] == 401


def test_login_token_has_no_email(clean_users):
    _seed_user("a@b.c", "SenhaForte#2026", role="viewer")
    token = _data(u.login(_pub({"email": "a@b.c", "password": "SenhaForte#2026"}), None))["access_token"]
    claims = jwt.decode(token, os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
    assert "email" not in claims and claims["role"] == "viewer"


def test_login_token_carries_organization_id(clean_users):
    u.create_user(_pub({"email": "org@b.c", "password": "SenhaForte#2026", "name": "Org"}), None)
    token = _data(u.login(_pub({"email": "org@b.c", "password": "SenhaForte#2026"}), None))["access_token"]
    claims = jwt.decode(token, os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
    assert "organization_id" in claims and uuid.UUID(claims["organization_id"])


# ── /auth/me ────────────────────────────────────────────────────────────────
def test_me_returns_current_user(clean_users):
    uid = _seed_user("me@b.c", "SenhaForte#2026", role="analyst")
    resp = u.me(_event(uid, role="analyst"), None)
    data = _data(resp)
    assert data["id"] == uid and data["email"] == "me@b.c"
    assert data["role"] == "analyst" and data["organization_id"] == SYSTEM_ORG_ID


def test_me_unauthenticated_401(clean_users):
    assert u.me({"requestContext": {}}, None)["statusCode"] == 401


# ── RBAC: get / list ───────────────────────────────────────────────────────
def test_get_user_self_and_admin(clean_users):
    # callers reais no banco (o recheck exige usuário existente e ativo — SEC-04)
    uid = _seed_user("a@b.c", "SenhaForte#2026")            # viewer alvo
    viewer2 = _seed_user("v2@b.c", "SenhaForte#2026", role="viewer")
    admin = _seed_user("adm@b.c", "SenhaForte#2026", role="admin")
    assert u.get_user(_event(uid, path={"userId": uid}), None)["statusCode"] == 200          # self
    assert u.get_user(_event(viewer2, path={"userId": uid}), None)["statusCode"] == 403       # viewer lê outro
    assert u.get_user(_event(admin, role="admin", path={"userId": uid}), None)["statusCode"] == 200


def test_list_users_admin_only(clean_users):
    _seed_user("a@b.c", "SenhaForte#2026")
    viewer = _seed_user("v@b.c", "SenhaForte#2026", role="viewer")
    admin = _seed_user("adm@b.c", "SenhaForte#2026", role="admin")
    assert u.list_users(_event(viewer, role="viewer"), None)["statusCode"] == 403
    assert u.list_users(_event(admin, role="admin"), None)["statusCode"] == 200


def test_list_users_only_same_org(clean_users):
    # cada signup cria a sua própria organização; um admin não enxerga users de outra org
    d1 = _data(u.create_user(_pub({"email": "o1@b.c", "password": "SenhaForte#2026", "name": "O1"}), None))
    d2 = _data(u.create_user(_pub({"email": "o2@b.c", "password": "SenhaForte#2026", "name": "O2"}), None))
    resp = u.list_users(_event(d1["user_id"], role="admin", org_id=d1["organization_id"]), None)
    ids = [x["id"] for x in _data(resp)]
    assert d1["user_id"] in ids and d2["user_id"] not in ids


def test_unauthenticated_blocked(clean_users):
    assert u.list_users({"requestContext": {}}, None)["statusCode"] == 401


# ── update / delete ────────────────────────────────────────────────────────
def test_non_admin_cannot_change_role(clean_users):
    uid = _seed_user("a@b.c", "SenhaForte#2026", role="viewer")
    resp = u.update_user(_event(uid, role="viewer", path={"userId": uid},
                                body={"name": "Novo", "role": "admin"}), None)
    assert resp["statusCode"] == 200  # name atualizado, role ignorado
    role = _row_role(uid)
    assert role == "viewer"


def test_admin_can_change_role(clean_users):
    uid = _seed_user("a@b.c", "SenhaForte#2026", role="viewer")
    admin = _seed_user("adm@b.c", "SenhaForte#2026", role="admin")  # caller admin real
    resp = u.update_user(_event(admin, role="admin", path={"userId": uid},
                                body={"role": "analyst"}), None)
    assert resp["statusCode"] == 200
    assert _row_role(uid) == "analyst"


def test_cannot_demote_last_admin(clean_users):
    # B1: rebaixar o único admin é bloqueado (evita lockout)
    admin = _seed_user("admin@b.c", "SenhaForte#2026", role="admin")
    resp = u.update_user(_event(admin, role="admin", path={"userId": admin},
                                body={"role": "viewer"}), None)
    assert resp["statusCode"] == 409
    assert _row_role(admin) == "admin"


def test_cannot_delete_last_admin(clean_users):
    admin = _seed_user("admin@b.c", "SenhaForte#2026", role="admin")
    # o próprio (único) admin tenta se desativar -> bloqueado (caller real e ativo)
    resp = u.delete_user(_event(admin, role="admin", path={"userId": admin}), None)
    assert resp["statusCode"] == 409


def test_can_demote_admin_when_another_exists(clean_users):
    a1 = _seed_user("a1@b.c", "SenhaForte#2026", role="admin")
    a2 = _seed_user("a2@b.c", "SenhaForte#2026", role="admin")
    resp = u.update_user(_event(a2, role="admin", path={"userId": a1},
                                body={"role": "analyst"}), None)
    assert resp["statusCode"] == 200
    assert _row_role(a1) == "analyst"


def test_delete_user_is_soft_and_admin_only(clean_users):
    uid = _seed_user("a@b.c", "SenhaForte#2026")                    # viewer alvo
    viewer = _seed_user("v@b.c", "SenhaForte#2026", role="viewer")
    admin = _seed_user("adm@b.c", "SenhaForte#2026", role="admin")
    assert u.delete_user(_event(viewer, role="viewer", path={"userId": uid}),
                         None)["statusCode"] == 403
    assert u.delete_user(_event(admin, role="admin", path={"userId": uid}),
                         None)["statusCode"] == 200
    # após soft delete (inactive), login falha
    assert u.login(_pub({"email": "a@b.c", "password": "SenhaForte#2026"}), None)["statusCode"] == 401


# ── SEC-01..04 / M2: revogação por request (token antigo não vale mais) ──────
def test_sec01_admin_rebaixado_no_banco_nao_lista(clean_users):
    admin = _seed_user("adm@b.c", "SenhaForte#2026", role="admin")
    _set_user(admin, role="viewer")  # rebaixado no banco; JWT antigo ainda diz admin
    assert u.list_users(_event(admin, role="admin"), None)["statusCode"] == 403


def test_sec01_admin_desativado_no_banco_nao_lista(clean_users):
    admin = _seed_user("adm@b.c", "SenhaForte#2026", role="admin")
    _set_user(admin, status="inactive")
    assert u.list_users(_event(admin, role="admin"), None)["statusCode"] == 403


def test_sec02_admin_rebaixado_nao_restaura_proprio_papel(clean_users):
    uid = _seed_user("adm@b.c", "SenhaForte#2026", role="admin")
    _set_user(uid, role="viewer")  # rebaixado; tenta se re-promover com JWT antigo
    resp = u.update_user(_event(uid, role="admin", path={"userId": uid},
                                body={"name": "Ainda Eu", "role": "admin"}), None)
    assert resp["statusCode"] == 200      # o name até atualiza...
    assert _row_role(uid) == "viewer"     # ...mas a auto-elevação é ignorada (papel do banco)


def test_sec03_admin_desativado_nao_deleta_outro(clean_users):
    admin = _seed_user("adm@b.c", "SenhaForte#2026", role="admin")
    alvo = _seed_user("v@b.c", "SenhaForte#2026", role="viewer")
    _set_user(admin, status="inactive")
    assert u.delete_user(_event(admin, role="admin", path={"userId": alvo}),
                         None)["statusCode"] == 403
    assert _row_status(alvo) == "active"  # não desativou o alvo


def test_sec04_admin_desativado_nao_le_terceiro(clean_users):
    admin = _seed_user("adm@b.c", "SenhaForte#2026", role="admin")
    alvo = _seed_user("v@b.c", "SenhaForte#2026", role="viewer")
    _set_user(admin, status="inactive")
    assert u.get_user(_event(admin, role="admin", path={"userId": alvo}),
                      None)["statusCode"] == 403


# ── forgot / reset ─────────────────────────────────────────────────────────
def _capture_token(monkeypatch, captured):
    monkeypatch.setattr(
        u.email_service, "send_reset_password_email",
        lambda to_email, reset_token, user_name=None: captured.update(token=reset_token) or True,
    )


def test_forgot_then_reset_flow(clean_users, monkeypatch):
    _seed_user("a@b.c", "SenhaForte#2026")
    captured = {}
    _capture_token(monkeypatch, captured)
    assert u.forgot_password(_pub({"email": "a@b.c"}), None)["statusCode"] == 200
    token = captured["token"]  # token em CLARO (como iria no e-mail)
    assert u.reset_password(_pub({"token": token, "password": "NovaSenha#2026"}), None)["statusCode"] == 200
    # login com a nova senha funciona; com a antiga, não
    assert u.login(_pub({"email": "a@b.c", "password": "NovaSenha#2026"}), None)["statusCode"] == 200
    assert u.login(_pub({"email": "a@b.c", "password": "SenhaForte#2026"}), None)["statusCode"] == 401
    # token é de uso único (consumo atômico): segundo uso falha
    assert u.reset_password(_pub({"token": token, "password": "Outra1234"}), None)["statusCode"] == 400


def test_auth02_reset_revoga_tokens_anteriores(clean_users):
    """AUTH-02: password_changed_at revoga tokens emitidos ANTES do reset. list_users
    reconsulta o caller (assert_active_admin -> load_active_caller): um token cujo iat é
    anterior à troca de senha vira 403; um posterior segue válido; sem iat (token
    pré-deploy, sem o claim) NÃO revoga (retrocompatível)."""
    uid = _seed_user("adm@x.c", "SenhaForte#2026", role="admin", status="active")
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE public.users SET password_changed_at = NOW() WHERE id = %s", (uid,))
        cur.execute("SELECT floor(extract(epoch FROM password_changed_at))::bigint"
                    " FROM public.users WHERE id = %s", (uid,))
        pca = int(cur.fetchone()[0])
    conn.close()

    def _ev(iat):
        ev = _event(uid, role="admin")
        ev["requestContext"]["authorizer"]["iat"] = iat
        return ev

    assert u.list_users(_ev(pca - 10), None)["statusCode"] == 403   # iat anterior => revogado
    assert u.list_users(_ev(pca + 10), None)["statusCode"] == 200   # iat posterior => ok
    assert u.list_users(_event(uid, role="admin"), None)["statusCode"] == 200  # sem iat => ok


def test_forgot_keeps_single_token_per_user(clean_users, monkeypatch):
    # B6: dois forgot seguidos não podem deixar 2 tokens válidos (UNIQUE user_id + upsert)
    uid = _seed_user("a@b.c", "SenhaForte#2026")
    _capture_token(monkeypatch, {})
    u.forgot_password(_pub({"email": "a@b.c"}), None)
    u.forgot_password(_pub({"email": "a@b.c"}), None)
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.password_resets WHERE user_id = %s", (uid,))
        n = cur.fetchone()[0]
    conn.close()
    assert n == 1  # apenas 1 token por usuário


def test_sec08_forgot_cooldown_nao_reenvia_nem_troca_token(clean_users, monkeypatch):
    # SEC-08: 2º forgot dentro do cooldown mantém resposta genérica, mas NÃO reenvia
    # e-mail nem troca o token vigente (anti-spam / anti-invalidação contínua).
    _seed_user("a@b.c", "SenhaForte#2026")
    calls = {"n": 0}

    def _send(to_email, reset_token, user_name=None):
        calls["n"] += 1
        return True

    monkeypatch.setattr(u.email_service, "send_reset_password_email", _send)
    assert u.forgot_password(_pub({"email": "a@b.c"}), None)["statusCode"] == 200
    token1 = _fetch_reset_token("a@b.c")
    # segundo forgot imediato (dentro do cooldown)
    assert u.forgot_password(_pub({"email": "a@b.c"}), None)["statusCode"] == 200
    assert calls["n"] == 1                        # e-mail enviado só na 1ª vez
    assert _fetch_reset_token("a@b.c") == token1  # token vigente preservado


def test_reset_token_stored_hashed(clean_users, monkeypatch):
    import hashlib
    _seed_user("a@b.c", "SenhaForte#2026")
    captured = {}
    _capture_token(monkeypatch, captured)
    u.forgot_password(_pub({"email": "a@b.c"}), None)
    clear = captured["token"]
    stored = _fetch_reset_token("a@b.c")
    assert stored != clear  # nunca armazenado em claro
    assert stored == hashlib.sha256(clear.encode()).hexdigest()


def test_forgot_unknown_email_is_generic(clean_users):
    assert u.forgot_password(_pub({"email": "naoexiste@b.c"}), None)["statusCode"] == 200


def test_forgot_returns_200_even_if_send_fails(clean_users, monkeypatch):
    # falha de envio não pode mudar a resposta (anti-enumeração); é só logada
    _seed_user("a@b.c", "SenhaForte#2026")
    monkeypatch.setattr(u.email_service, "send_reset_password_email", lambda *a, **k: False)
    assert u.forgot_password(_pub({"email": "a@b.c"}), None)["statusCode"] == 200


def test_reset_invalid_token_400(clean_users):
    assert u.reset_password(_pub({"token": "x" * 30, "password": "NovaSenha#2026"}),
                            None)["statusCode"] == 400


# ── helpers de verificação no banco ────────────────────────────────────────
def _row_role(uid):
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT role FROM public.users WHERE id = %s", (uid,))
        role = cur.fetchone()[0]
    conn.close()
    return role


def _row_status(uid):
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM public.users WHERE id = %s", (uid,))
        status = cur.fetchone()[0]
    conn.close()
    return status


def _set_user(uid, role=None, status=None):
    """Muda role/status direto no banco (simula revogação após emissão do JWT)."""
    sets, vals = [], []
    if role is not None:
        sets.append("role = %s")
        vals.append(role)
    if status is not None:
        sets.append("status = %s")
        vals.append(status)
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"UPDATE public.users SET {', '.join(sets)} WHERE id = %s", (*vals, uid))
    conn.close()


def _fetch_reset_token(email):
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pr.token FROM public.password_resets pr"
            " JOIN public.users us ON us.id = pr.user_id WHERE us.email = %s", (email,))
        row = cur.fetchone()
    conn.close()
    return row[0] if row else None
