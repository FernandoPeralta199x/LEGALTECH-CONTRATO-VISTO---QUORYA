"""Varredura profunda — edge cases de input e robustez.

Casos que podem quebrar os handlers: body JSON não-objeto, valores limites, etc.
"""
import json
import uuid

import psycopg2
import pytest

from src.handlers import case_results, cases, clients, documents, search, users
from src.utils import lambda_io

_UID = str(uuid.uuid4())
_ORG = "00000000-0000-0000-0000-000000000001"


def _ev(body, role="analyst", path=None, query=None):
    return {
        "requestContext": {"authorizer": {"user_id": _UID, "role": role, "organization_id": _ORG}},
        "body": body,
        "pathParameters": path or {},
        "queryStringParameters": query or {},
    }


# body JSON válido mas que NÃO é objeto: deve dar 400, nunca exceção/500.
_NON_OBJECT_BODIES = ["[]", "123", '"texto"', "null", "true"]
_BODY_HANDLERS = [
    cases.create_case, clients.create_client, users.create_user,
    documents.upload_document, search.search_clauses, case_results.create_case_result,
]


@pytest.mark.parametrize("handler", _BODY_HANDLERS, ids=lambda h: h.__name__)
@pytest.mark.parametrize("body", _NON_OBJECT_BODIES)
def test_non_object_body_returns_400(handler, body):
    resp = handler(_ev(body), None)
    assert resp["statusCode"] == 400, (handler.__name__, body, resp)


# ── Lambda + RLS: a peça central (conexão global reusada entre invocações) ────
def test_rls_context_does_not_leak_between_transactions():
    """SET LOCAL (set_config is_local=true) não pode vazar p/ a próxima invocação
    que reusa a mesma conexão global — senão um usuário veria dados de outro."""
    from src.services.database import get_connection, tenant_tx
    a = str(uuid.uuid4())
    with tenant_tx(a, "analyst", _ORG) as cur:
        cur.execute("SELECT current_setting('app.user_id', true)")
        assert cur.fetchone()["current_setting"] == a  # ativo dentro da tx
    # depois do commit, na MESMA conexão, o contexto não persiste
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT current_setting('app.user_id', true) AS v")
    leaked = c.fetchone()[0]
    c.close()
    conn.rollback()
    assert leaked in (None, ""), f"contexto RLS vazou entre invocações: {leaked!r}"


def test_connection_recovers_after_aborted_transaction():
    """Se uma invocação aborta a transação, a conexão global reusada deve voltar
    utilizável na próxima (get_connection faz rollback antes de usar)."""
    from src.services.database import tenant_tx
    a = str(uuid.uuid4())
    try:
        with tenant_tx(a, "analyst", _ORG) as cur:
            cur.execute("SELECT 1/0")  # erro de banco -> transação abortada
    except Exception:
        pass
    with tenant_tx(a, "analyst", _ORG) as cur:  # próxima invocação reusa a conexão
        cur.execute("SELECT 1 AS v")
        assert cur.fetchone()["v"] == 1


# ── limites numéricos (validação Pydantic) ───────────────────────────────────
def test_confidence_score_out_of_range_400():
    body = json.dumps({"case_id": str(uuid.uuid4()), "result_type": "dd",
                       "findings": {}, "risk_level": "low", "confidence_score": 2.0})
    assert case_results.create_case_result(_ev(body), None)["statusCode"] == 400


def test_search_top_k_out_of_range_400():
    body = json.dumps({"case_id": str(uuid.uuid4()), "query": "x", "top_k": 999})
    assert search.search_clauses(_ev(body), None)["statusCode"] == 400


# ── segurança: injection e unicode em campos de texto ────────────────────────
@pytest.fixture()
def _clean_clients():
    conn = psycopg2.connect(host="localhost", port=5433, user="dbadmin",
                            password="localdev_cv", dbname="contrato_visto")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.cases, public.clients RESTART IDENTITY CASCADE")
    conn.close()


def _create_client(legal_name):
    body = json.dumps({"legal_name": legal_name, "document_type": "cnpj",
                       "document_number": "11.222.333/0001-81"})
    return clients.create_client(_ev(body), None)


def test_sql_injection_in_text_is_neutralized(_clean_clients):
    evil = "x'); DROP TABLE public.clients; --"
    resp = _create_client(evil)
    assert resp["statusCode"] == 201
    cid = json.loads(resp["body"])["data"]["id"]
    got = clients.get_client(_ev(None, path={"clientId": cid}), None)
    # tabela sobreviveu (não foi dropada) e o valor é literal
    assert got["statusCode"] == 200
    assert json.loads(got["body"])["data"]["legal_name"] == evil


# parse_json_body deve barrar valores que o Python aceita mas o Postgres rejeita
# (NaN/Infinity no jsonb; null byte em text) — senão viram 500 no INSERT.
def _body_ev(s):
    return {"body": s}


def test_parse_rejects_nan():
    data, err = lambda_io.parse_json_body(_body_ev('{"x": NaN}'))
    assert data is None and err["statusCode"] == 400


def test_parse_rejects_infinity():
    data, err = lambda_io.parse_json_body(_body_ev('{"x": Infinity}'))
    assert data is None and err["statusCode"] == 400


def test_parse_rejects_null_byte():
    data, err = lambda_io.parse_json_body(_body_ev(json.dumps({"x": "a" + chr(0) + "b"})))
    assert data is None and err["statusCode"] == 400


def test_parse_accepts_normal_body():
    data, err = lambda_io.parse_json_body(_body_ev('{"x": 1, "y": "ok"}'))
    assert err is None and data == {"x": 1, "y": "ok"}


def test_null_byte_in_valid_field_returns_400(_clean_clients):
    # campo válido (legal_name) com null byte: deve dar 400, não 500
    body = json.dumps({"legal_name": "a" + chr(0) + "b", "document_type": "cnpj",
                       "document_number": "11.222.333/0001-81"})
    assert clients.create_client(_ev(body), None)["statusCode"] == 400


def test_page_overflow_does_not_500():
    r = cases.list_cases(_ev(None, query={"page": "9" * 25}), None)
    assert r["statusCode"] in (200, 400)  # nunca 500


def test_oversized_body_returns_413():
    big = json.dumps({"x": "a" * 1_100_000})  # > 1 MB
    assert cases.create_case(_ev(big), None)["statusCode"] == 413


def test_authorizer_requires_exp():
    import os

    import jwt as _jwt

    from src.authorizers import jwt_authorizer
    tok = _jwt.encode({"user_id": "x", "role": "admin"},  # sem exp
                      os.environ["JWT_SECRET_KEY"], algorithm="HS256")
    res = jwt_authorizer.authorize(
        {"type": "TOKEN", "authorizationToken": f"Bearer {tok}",
         "methodArn": "arn:aws:execute-api:sa-east-1:1:a/dev/GET/x"}, None)
    assert res["policyDocument"]["Statement"][0]["Effect"] == "Deny"


def test_unicode_preserved(_clean_clients):
    name = "Açaí Müller 日本 😀 Ltda"
    resp = _create_client(name)
    assert resp["statusCode"] == 201
    cid = json.loads(resp["body"])["data"]["id"]
    got = json.loads(clients.get_client(_ev(None, path={"clientId": cid}), None)["body"])["data"]
    assert got["legal_name"] == name
