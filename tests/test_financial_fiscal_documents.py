"""Testes do handler de Notas Fiscais (Fase 5) — fiscal_documents (PG18 + RLS).

O sistema REGISTRA notas já emitidas; não emite nem gera número. Cobre: agregação,
anti-forja (authorized exige número; rejected exige motivo; URL só https/s3;
número duplicado → 409), admin+revogação, RLS, sem PII denormalizada e auditoria.
"""
from _dbadmin import admin_conn
import json
import uuid

import pytest

from src.handlers import financial as fin_h
from src.handlers import financial_fiscal as ff_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _reset():
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.fiscal_documents RESTART IDENTITY CASCADE")
    conn.close()


def _seed_user(user_id, org=SYSTEM_ORG, role="admin", status="active"):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s,%s,'x','Test',%s,%s,%s)"
            " ON CONFLICT (id) DO UPDATE SET role=EXCLUDED.role, status=EXCLUDED.status",
            (user_id, f"u_{user_id}@t.c", role, status, org))
    conn.close()


def _admin(org=SYSTEM_ORG):
    uid = str(uuid.uuid4())
    _seed_user(uid, org=org)
    return uid


def _event(user_id, role="admin", org_id=SYSTEM_ORG, query=None, body=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "perfil": "administrador", "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {},
        "queryStringParameters": query or {},
    }


def _data(resp, code=200):
    assert resp["statusCode"] == code, resp
    return json.loads(resp["body"]).get("data")


def _note(**over):
    base = {"doc_type": "nfse", "amount_cents": 10000, "status": "note_pending"}
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_fiscal_vazio():
    a = _admin()
    d = _data(ff_h.list_fiscal_documents(_event(a), None))
    assert d["items"] == [] and d["total"] == 0
    assert d["summary"]["invoices_authorized_count"] == 0
    assert "disclaimer" in d


def test_fiscal_criar_e_listar():
    a = _admin()
    created = _data(ff_h.create_fiscal_document(_event(a, body=_note()), None), code=201)
    assert created["doc_type"] == "nfse"
    d = _data(ff_h.list_fiscal_documents(_event(a), None))
    assert d["total"] == 1
    assert d["summary"]["invoices_pending_count"] == 1


def test_fiscal_authorized_exige_numero():
    a = _admin()
    # authorized sem número → 400 (não forja numeração fiscal)
    assert ff_h.create_fiscal_document(_event(a, body=_note(status="authorized")), None)["statusCode"] == 400
    ok = ff_h.create_fiscal_document(_event(a, body=_note(status="authorized", numero="123", issued_at="2026-07-10")), None)
    assert ok["statusCode"] == 201


def test_fiscal_authorized_seta_authorized_at():
    a = _admin()
    # authorized sem issued_at → authorized_at ainda é preenchido (coerência)
    created = _data(ff_h.create_fiscal_document(_event(a, body=_note(status="authorized", numero="99")), None), code=201)
    assert created["authorized_at"] is not None


def test_fiscal_note_canceled_conta():
    a = _admin()
    ff_h.create_fiscal_document(_event(a, body=_note(status="note_canceled")), None)
    assert _data(ff_h.list_fiscal_documents(_event(a), None))["summary"]["invoices_canceled_count"] == 1


def test_fiscal_rejected_exige_motivo():
    a = _admin()
    assert ff_h.create_fiscal_document(_event(a, body=_note(status="rejected")), None)["statusCode"] == 400
    ok = ff_h.create_fiscal_document(_event(a, body=_note(status="rejected", rejection_reason="CNPJ inválido")), None)
    assert ok["statusCode"] == 201


def test_fiscal_numero_duplicado():
    a = _admin()
    assert ff_h.create_fiscal_document(_event(a, body=_note(status="authorized", numero="777", serie="1")), None)["statusCode"] == 201
    # mesmo (doc_type, serie, numero) na org → 409 (não 400 de vínculo)
    assert ff_h.create_fiscal_document(_event(a, body=_note(status="authorized", numero="777", serie="1")), None)["statusCode"] == 409


def test_db01_fiscal_numero_duplicado_sem_serie():
    """DB-01: SEM serie (NULL), duas notas com o mesmo numero DEVEM colidir
    (uq_fiscal_documents_org_numero NULLS NOT DISTINCT). Antes, a companheira serie
    NULL era tratada como distinta e a NF duplicava silenciosamente."""
    a = _admin()
    assert ff_h.create_fiscal_document(
        _event(a, body=_note(status="authorized", numero="888")), None)["statusCode"] == 201
    assert ff_h.create_fiscal_document(
        _event(a, body=_note(status="authorized", numero="888")), None)["statusCode"] == 409


def test_fiscal_url_scheme_seguro():
    a = _admin()
    assert ff_h.create_fiscal_document(_event(a, body=_note(document_url="javascript:alert(1)")), None)["statusCode"] == 400
    ok = ff_h.create_fiscal_document(_event(a, body=_note(document_url="https://bucket.s3/nota.pdf")), None)
    assert ok["statusCode"] == 201


def test_fiscal_valida():
    a = _admin()
    assert ff_h.create_fiscal_document(_event(a, body=_note(doc_type="xpto")), None)["statusCode"] == 400
    assert ff_h.create_fiscal_document(_event(a, body=_note(amount_cents=-1)), None)["statusCode"] == 400
    assert ff_h.create_fiscal_document(_event(a, body=_note(status="xpto")), None)["statusCode"] == 400
    assert ff_h.create_fiscal_document(_event(a, body=_note(campo_extra=1)), None)["statusCode"] == 400  # extra=forbid


def test_fiscal_admin_only():
    a = _admin()
    assert ff_h.create_fiscal_document(_event(a, role="viewer", body=_note()), None)["statusCode"] == 403
    assert ff_h.create_fiscal_document(_event(a, role="analyst", body=_note()), None)["statusCode"] == 403


def test_fiscal_revogacao_403():
    uid = str(uuid.uuid4())
    _seed_user(uid, status="inactive")
    assert ff_h.create_fiscal_document(_event(uid, body=_note()), None)["statusCode"] == 403
    ghost = str(uuid.uuid4())
    assert ff_h.create_fiscal_document(_event(ghost, body=_note()), None)["statusCode"] == 403


def test_fiscal_isolado_por_org():
    a = _admin()
    ff_h.create_fiscal_document(_event(a, body=_note()), None)
    b = _admin(org=OTHER_ORG)  # admin ATIVO na outra org: o 0 vem da RLS, nao do 403 (SEC-02b)
    assert _data(ff_h.list_fiscal_documents(_event(b, org_id=OTHER_ORG), None))["total"] == 0


def test_fiscal_vinculo_invalido():
    a = _admin()
    assert ff_h.create_fiscal_document(_event(a, body=_note(case_id=str(uuid.uuid4()))), None)["statusCode"] == 400


def test_fiscal_nao_denormaliza_pii():
    a = _admin()
    created = _data(ff_h.create_fiscal_document(_event(a, body=_note()), None), code=201)
    # expõe client_id (uuid, FK) mas NÃO CPF/nome do cliente
    for pii in ("cpf", "document_number", "client_name", "rg"):
        assert pii not in created
    assert "client_id" in created


def test_fiscal_auditoria():
    a = _admin()
    created = _data(ff_h.create_fiscal_document(_event(a, body=_note()), None), code=201)
    conn = admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit.audit_log WHERE resource_type='fiscal_documents'"
                    " AND resource_id=%s AND action='INSERT'", (created["id"],))
        n = cur.fetchone()[0]
    conn.close()
    assert n == 1


def test_overview_integra_invoices_count():
    a = _admin()
    ff_h.create_fiscal_document(_event(a, body=_note(status="authorized", numero="A1", issuance_cost_cents=300)), None)
    ff_h.create_fiscal_document(_event(a, body=_note(status="authorized", numero="A2", issuance_cost_cents=300)), None)
    ff_h.create_fiscal_document(_event(a, body=_note(status="note_pending")), None)
    kpis = _data(fin_h.get_overview(_event(a), None))["kpis"]
    assert kpis["invoices_count"] == 2                     # só authorized
    assert kpis["invoices_issuance_cost_cents"] == 600
