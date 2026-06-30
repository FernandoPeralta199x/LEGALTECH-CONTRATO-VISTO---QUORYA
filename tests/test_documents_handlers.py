"""Fase 4 — testes de integração dos handlers de documents (PG18 + RLS).

`public.documents` tem RLS por `uploaded_by`. Upload exige case visível + papel
writer; download/get filtrado pela RLS (dono ou admin). S3 via backend `local`.
"""
import json
import uuid

import psycopg2
import pytest

from src.handlers import cases as cases_h
from src.handlers import documents as d


def _admin_conn():
    return psycopg2.connect(
        host="localhost", port=5433, user="dbadmin",
        password="localdev_cv", dbname="contrato_visto", connect_timeout=5,
    )


@pytest.fixture()
def client_id():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.documents, public.cases, public.clients"
                    " RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE audit.audit_log RESTART IDENTITY")
        cur.execute(
            "INSERT INTO public.clients (legal_name, document_number, document_type)"
            " VALUES ('Cliente Teste', %s, 'cnpj') RETURNING id",
            (uuid.uuid4().hex[:14],),
        )
        cid = cur.fetchone()[0]
    conn.close()
    return str(cid)


def _event(user_id, role="analyst", body=None, path=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c", "role": role, "organization_id": "00000000-0000-0000-0000-000000000001"}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


def _make_case(user_id, client_id, role="analyst"):
    resp = cases_h.create_case(
        _event(user_id, role, body={"client_id": client_id, "case_type": "contract_analysis"}),
        None,
    )
    return _data(resp)["id"]


def _upload(user_id, case_id, role="analyst", **over):
    body = {"case_id": case_id, "file_name": "contrato.pdf", "file_type": "pdf", **over}
    return d.upload_document(_event(user_id, role, body=body), None)


def test_upload_and_get(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    up = _upload(a, case_id, file_size_bytes=1024)
    assert up["statusCode"] == 201
    out = _data(up)
    assert out["upload_url"].startswith("https://") and out["s3_path"].startswith("cases/")
    doc_id = out["document_id"]

    got = _data(d.get_document(_event(a, path={"docId": doc_id}), None))
    assert got["file_name"] == "contrato.pdf" and got["ocr_status"] == "pending"
    assert got["download_url"].startswith("https://")


def test_upload_requires_visible_case(client_id):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    # B não enxerga o case de A (RLS de cases) → upload bloqueado
    assert _upload(b, case_id)["statusCode"] == 404


def test_upload_nonexistent_case_404(client_id):
    a = str(uuid.uuid4())
    assert _upload(a, str(uuid.uuid4()))["statusCode"] == 404


def test_viewer_cannot_upload(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    assert _upload(a, case_id, role="viewer")["statusCode"] == 403


def test_document_isolation_by_owner(client_id):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id))["document_id"]
    # B não é dono do documento (RLS uploaded_by) → 404; admin vê
    assert d.get_document(_event(b, path={"docId": doc_id}), None)["statusCode"] == 404
    assert d.get_document(_event(b, role="admin", path={"docId": doc_id}), None)["statusCode"] == 200


def test_invalid_file_type_400(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    assert _upload(a, case_id, file_type="exe")["statusCode"] == 400


def test_upload_blocked_on_finalized_case(client_id):
    # B4: case finalizado (completed) não aceita novos documentos
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    cases_h.update_case(_event(a, path={"caseId": case_id}, body={"status": "completed"}), None)
    assert _upload(a, case_id)["statusCode"] == 409


def test_unauthenticated_blocked(client_id):
    assert d.get_document({"requestContext": {}}, None)["statusCode"] == 401
