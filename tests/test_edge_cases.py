"""Varredura profunda — edge cases de input e robustez.

Casos que podem quebrar os handlers: body JSON não-objeto, valores limites, etc.
"""
import json
import uuid

import pytest

from src.handlers import case_results, cases, clients, documents, search, users

_UID = str(uuid.uuid4())


def _ev(body, role="analyst", path=None, query=None):
    return {
        "requestContext": {"authorizer": {"user_id": _UID, "role": role}},
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
