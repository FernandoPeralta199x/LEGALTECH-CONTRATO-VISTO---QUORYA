"""Smoke test do dev-server local (DEV-ONLY): roteamento, prefixo /api/v1,
simulação do JWT Authorizer e conversão HTTP<->event ponta a ponta."""
import json
import threading
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer

import psycopg2
import pytest

from src.handlers.users import hash_password
from tools.local_server import _Handler

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(scope="module")
def base_url():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _seed_admin(email, password):
    conn = psycopg2.connect(host="localhost", port=5433, user="dbadmin",
                            password="localdev_cv", dbname="contrato_visto", connect_timeout=5)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s,%s,%s,'Dev','admin','active',%s)",
            (str(uuid.uuid4()), email, hash_password(password), SYSTEM_ORG))
    conn.close()


def _req(base, method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw else {})


def test_dev_server_login_me_pricing(base_url):
    email = f"dev_{uuid.uuid4().hex[:8]}@b.c"
    _seed_admin(email, "Senha123")

    # login via alias /api/v1/auth/login (prefixo normalizado)
    st, payload = _req(base_url, "POST", "/api/v1/auth/login",
                       {"email": email, "password": "Senha123"})
    assert st == 200, payload
    token = payload["data"]["access_token"]
    assert payload["data"]["user"]["email"] == email

    # /auth/me autenticado (authorizer simulado injeta os claims)
    st, me = _req(base_url, "GET", "/api/v1/auth/me", token=token)
    assert st == 200 and me["data"]["email"] == email

    # /auth/me sem token -> 401 (rota protegida)
    st, _ = _req(base_url, "GET", "/api/v1/auth/me")
    assert st == 401

    # catálogo de pricing autenticado
    st, cat = _req(base_url, "GET", "/api/v1/pricing", token=token)
    assert st == 200 and len(cat["data"]["products"]) == 4

    # rota inexistente -> 404
    st, _ = _req(base_url, "GET", "/api/v1/nao-existe", token=token)
    assert st == 404


def test_dev_server_cors_preflight(base_url):
    req = urllib.request.Request(base_url + "/api/v1/pricing", method="OPTIONS")
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 204
        assert r.headers.get("Access-Control-Allow-Origin") == "*"
