"""Fase A — testes de integração dos handlers de users/auth (PG18).

`public.users` não tem RLS; valida signup (sempre viewer), login (JWT), RBAC
(viewer/admin), update/delete e o fluxo forgot→reset. Handlers protegidos são
invocados com o `event` que o API Gateway entrega (claims achatados).
"""
import json
import os
import uuid

import jwt
import psycopg2
import pytest

from src.handlers import users as u
from src.handlers.users import hash_password


def _admin_conn():
    return psycopg2.connect(
        host="localhost", port=5433, user="dbadmin",
        password="localdev_cv", dbname="contrato_visto", connect_timeout=5,
    )


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
            "INSERT INTO public.users (id, email, password_hash, name, role, status)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (uid, email, hash_password(password), "Test", role, status),
        )
    conn.close()
    return uid


def _event(user_id, role="viewer", body=None, path=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c", "role": role}},
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
def test_signup_creates_viewer(clean_users):
    resp = u.create_user(_pub({"email": "a@b.c", "password": "Senha123", "name": "Ana"}), None)
    assert resp["statusCode"] == 201
    assert _data(resp)["role"] == "viewer"


def test_signup_ignores_role_field(clean_users):
    # `role` não é aceito no schema (extra=forbid) → 400, sem virar admin.
    resp = u.create_user(
        _pub({"email": "a@b.c", "password": "Senha123", "name": "Ana", "role": "admin"}), None
    )
    assert resp["statusCode"] == 400


def test_signup_duplicate_email_409(clean_users):
    u.create_user(_pub({"email": "a@b.c", "password": "Senha123", "name": "Ana"}), None)
    resp = u.create_user(_pub({"email": "a@b.c", "password": "Senha123", "name": "Bia"}), None)
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
    _seed_user("a@b.c", "Senha123", role="analyst")
    resp = u.login(_pub({"email": "a@b.c", "password": "Senha123"}), None)
    token = _data(resp)["token"]
    claims = jwt.decode(token, os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
    assert claims["role"] == "analyst" and "exp" in claims


def test_login_wrong_password_401(clean_users):
    _seed_user("a@b.c", "Senha123")
    assert u.login(_pub({"email": "a@b.c", "password": "Errada123"}), None)["statusCode"] == 401


def test_login_inactive_user_401(clean_users):
    _seed_user("a@b.c", "Senha123", status="inactive")
    assert u.login(_pub({"email": "a@b.c", "password": "Senha123"}), None)["statusCode"] == 401


def test_login_unknown_email_401(clean_users):
    # usuário inexistente: ainda 401 (e executa bcrypt dummy p/ tempo uniforme)
    assert u.login(_pub({"email": "x@y.z", "password": "Senha123"}), None)["statusCode"] == 401


def test_login_token_has_no_email(clean_users):
    _seed_user("a@b.c", "Senha123", role="viewer")
    token = _data(u.login(_pub({"email": "a@b.c", "password": "Senha123"}), None))["token"]
    claims = jwt.decode(token, os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
    assert "email" not in claims and claims["role"] == "viewer"


# ── RBAC: get / list ───────────────────────────────────────────────────────
def test_get_user_self_and_admin(clean_users):
    uid = _seed_user("a@b.c", "Senha123")
    other = str(uuid.uuid4())
    assert u.get_user(_event(uid, path={"userId": uid}), None)["statusCode"] == 200
    assert u.get_user(_event(other, path={"userId": uid}), None)["statusCode"] == 403
    assert u.get_user(_event(other, role="admin", path={"userId": uid}), None)["statusCode"] == 200


def test_list_users_admin_only(clean_users):
    _seed_user("a@b.c", "Senha123")
    assert u.list_users(_event(str(uuid.uuid4()), role="viewer"), None)["statusCode"] == 403
    assert u.list_users(_event(str(uuid.uuid4()), role="admin"), None)["statusCode"] == 200


def test_unauthenticated_blocked(clean_users):
    assert u.list_users({"requestContext": {}}, None)["statusCode"] == 401


# ── update / delete ────────────────────────────────────────────────────────
def test_non_admin_cannot_change_role(clean_users):
    uid = _seed_user("a@b.c", "Senha123", role="viewer")
    resp = u.update_user(_event(uid, role="viewer", path={"userId": uid},
                                body={"name": "Novo", "role": "admin"}), None)
    assert resp["statusCode"] == 200  # name atualizado, role ignorado
    role = _row_role(uid)
    assert role == "viewer"


def test_admin_can_change_role(clean_users):
    uid = _seed_user("a@b.c", "Senha123", role="viewer")
    admin = str(uuid.uuid4())
    resp = u.update_user(_event(admin, role="admin", path={"userId": uid},
                                body={"role": "analyst"}), None)
    assert resp["statusCode"] == 200
    assert _row_role(uid) == "analyst"


def test_delete_user_is_soft_and_admin_only(clean_users):
    uid = _seed_user("a@b.c", "Senha123")
    assert u.delete_user(_event(str(uuid.uuid4()), role="viewer", path={"userId": uid}),
                         None)["statusCode"] == 403
    assert u.delete_user(_event(str(uuid.uuid4()), role="admin", path={"userId": uid}),
                         None)["statusCode"] == 200
    # após soft delete (inactive), login falha
    assert u.login(_pub({"email": "a@b.c", "password": "Senha123"}), None)["statusCode"] == 401


# ── forgot / reset ─────────────────────────────────────────────────────────
def _capture_token(monkeypatch, captured):
    monkeypatch.setattr(
        u.email_service, "send_reset_password_email",
        lambda to_email, reset_token, user_name=None: captured.update(token=reset_token) or True,
    )


def test_forgot_then_reset_flow(clean_users, monkeypatch):
    _seed_user("a@b.c", "Senha123")
    captured = {}
    _capture_token(monkeypatch, captured)
    assert u.forgot_password(_pub({"email": "a@b.c"}), None)["statusCode"] == 200
    token = captured["token"]  # token em CLARO (como iria no e-mail)
    assert u.reset_password(_pub({"token": token, "password": "NovaSenha9"}), None)["statusCode"] == 200
    # login com a nova senha funciona; com a antiga, não
    assert u.login(_pub({"email": "a@b.c", "password": "NovaSenha9"}), None)["statusCode"] == 200
    assert u.login(_pub({"email": "a@b.c", "password": "Senha123"}), None)["statusCode"] == 401
    # token é de uso único (consumo atômico): segundo uso falha
    assert u.reset_password(_pub({"token": token, "password": "Outra1234"}), None)["statusCode"] == 400


def test_reset_token_stored_hashed(clean_users, monkeypatch):
    import hashlib
    _seed_user("a@b.c", "Senha123")
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
    _seed_user("a@b.c", "Senha123")
    monkeypatch.setattr(u.email_service, "send_reset_password_email", lambda *a, **k: False)
    assert u.forgot_password(_pub({"email": "a@b.c"}), None)["statusCode"] == 200


def test_reset_invalid_token_400(clean_users):
    assert u.reset_password(_pub({"token": "x" * 30, "password": "NovaSenha9"}),
                            None)["statusCode"] == 400


# ── helpers de verificação no banco ────────────────────────────────────────
def _row_role(uid):
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT role FROM public.users WHERE id = %s", (uid,))
        role = cur.fetchone()[0]
    conn.close()
    return role


def _fetch_reset_token(email):
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pr.token FROM public.password_resets pr"
            " JOIN public.users us ON us.id = pr.user_id WHERE us.email = %s", (email,))
        row = cur.fetchone()
    conn.close()
    return row[0] if row else None
