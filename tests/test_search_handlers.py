"""Fase 5 — testes de integração da busca semântica (PG18 + pgvector + RLS).

Usa embeddings MOCK (determinísticos) para exercitar a busca vetorial real no
pgvector. A RLS de `documents` (via tenant_tx) restringe a busca ao que o usuário vê.
"""
import json
import uuid

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

from src.handlers import cases as cases_h
from src.handlers import documents as docs_h
from src.handlers import search as search_h
from src.services import rag
from src.services.embeddings import embeddings_service

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


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
        cur.execute("TRUNCATE public.document_embeddings, public.document_chunks,"
                    " public.documents, public.cases, public.clients"
                    " RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE audit.audit_log RESTART IDENTITY")
        cur.execute("SELECT set_config('app.organization_id', %s, false)", (SYSTEM_ORG,))
        cur.execute(
            "INSERT INTO public.clients (organization_id, legal_name, document_number, document_type)"
            " VALUES (%s, 'Cliente Teste', %s, 'cnpj') RETURNING id",
            (SYSTEM_ORG, uuid.uuid4().hex[:14]),
        )
        cid = cur.fetchone()[0]
    conn.close()
    return str(cid)


def _event(user_id, role="analyst", body=None, org_id=SYSTEM_ORG):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c", "role": role, "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {}, "queryStringParameters": {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


def _make_case(user_id, client_id):
    return _data(cases_h.create_case(
        _event(user_id, body={"client_id": client_id, "case_type": "contract_analysis"}),
        None))["id"]


def _seed_doc_with_embeddings(user_id, case_id, segments):
    # documento criado pelo handler (uploaded_by = user_id) + chunks/embeddings (admin)
    up = docs_h.upload_document(
        _event(user_id, body={"case_id": case_id, "file_name": "doc.pdf", "file_type": "pdf"}),
        None)
    doc_id = _data(up)["document_id"]
    conn = _admin_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=RealDictCursor)
    for i, text in enumerate(segments):
        chunk_id = rag.store_chunk(cur, doc_id, i, text)
        rag.store_embedding(cur, doc_id, chunk_id, text, embeddings_service.embed(text))
    cur.close()
    conn.close()
    return doc_id


_SEGMENTS = ["contrato de locação comercial",
             "cláusula de rescisão antecipada com multa",
             "objeto e valor do contrato"]


def test_search_ranks_most_similar_first(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    _seed_doc_with_embeddings(a, case_id, _SEGMENTS)

    resp = search_h.search_clauses(
        _event(a, body={"case_id": case_id, "query": "cláusula de rescisão antecipada com multa"}),
        None)
    results = _data(resp)
    assert len(results) == 3
    # o segmento idêntico à query é o mais similar (cosine ~ 1.0)
    assert results[0]["segment_text"] == "cláusula de rescisão antecipada com multa"
    assert results[0]["similarity"] > 0.99


def test_search_is_rls_filtered(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    _seed_doc_with_embeddings(a, case_id, _SEGMENTS)

    # usuário de OUTRA organização não vê o case de A (RLS por org) → 404
    blocked = search_h.search_clauses(
        _event(str(uuid.uuid4()), body={"case_id": case_id, "query": "rescisão"}, org_id=OTHER_ORG), None)
    assert blocked["statusCode"] == 404
    # admin da MESMA organização vê o case e os documentos
    seen = _data(search_h.search_clauses(
        _event(str(uuid.uuid4()), role="admin", body={"case_id": case_id, "query": "rescisão"}), None))
    assert len(seen) == 3


def test_search_validation(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    # query vazia → 400
    assert search_h.search_clauses(
        _event(a, body={"case_id": case_id, "query": ""}), None)["statusCode"] == 400
    # case_id inválido → 400
    assert search_h.search_clauses(
        _event(a, body={"case_id": "nao-uuid", "query": "x"}), None)["statusCode"] == 400


def test_search_unauthenticated_blocked(client_id):
    assert search_h.search_clauses({"requestContext": {}}, None)["statusCode"] == 401
