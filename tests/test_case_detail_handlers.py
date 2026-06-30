"""Coração 5 — testes de leitura do detalhe do caso (PG18 + RLS).

Cria um pedido via wizard (que popula partes/timeline/triagem) e valida as abas:
partes (com PII MASCARADA), timeline, triagem, e o GET /cases/{id} enriquecido
(campos de produto + contagens + pricing). Cobre isolamento por org e 404.
"""
import json
import uuid

import psycopg2
import pytest

from src.handlers import case_parties as cp_h
from src.handlers import cases as cases_h
from src.handlers import requests as req_h
from src.handlers import timeline as tl_h
from src.handlers import triage as tr_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _admin_conn():
    return psycopg2.connect(host="localhost", port=5433, user="dbadmin",
                            password="localdev_cv", dbname="contrato_visto", connect_timeout=5)


def _reset():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.timeline_events, public.triage_modules, public.case_parties,"
            " public.documents, public.requests, public.cases, public.clients,"
            " public.request_code_sequences, public.pricing_configs RESTART IDENTITY CASCADE")
    conn.close()


def _event(user_id, role="admin", path=None, org_id=SYSTEM_ORG, body=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


def _wizard_payload():
    return {
        "product_type": "analise_contratual",
        "title": "Contrato de prestação",
        "parties": [
            {"name": "Empresa X LTDA", "role": "contratante", "person_type": "company",
             "document": "12345678000199", "document_type": "cnpj",
             "email": "contato@empresax.com", "phone": "11987654321"},
            {"name": "João Silva", "role": "contratado", "person_type": "individual",
             "document": "12345678909"},
        ],
        "document": {"filename": "contrato.pdf", "size_bytes": 1024},
        "selected_modules": ["ia_deepseek", "analise_contratual_ia"],
    }


@pytest.fixture()
def case_id():
    _reset()
    a = str(uuid.uuid4())
    data = _data(req_h.create_request(_event(a, body=_wizard_payload()), None))
    return data["case_id"]


def test_parties_com_pii_mascarada(case_id):
    a = str(uuid.uuid4())
    parties = _data(cp_h.list_case_parties(_event(a, path={"caseId": case_id}), None))
    assert len(parties) == 2
    empresa = next(p for p in parties if p["party_type"] == "contratante")
    # PII nunca crua; só mascarada
    assert empresa["document"] is None
    assert empresa["document_masked"] == "**.***.***/****-99"  # CNPJ
    assert empresa["email"] is None
    assert empresa["email_masked"] == "c******@empresax.com"
    assert empresa["phone_masked"] == "(11) ****-**21"
    # metadata não vaza PII
    assert "email" not in empresa["metadata"]
    assert "phone" not in empresa["metadata"]
    assert empresa["metadata"].get("person_type") == "company"
    pessoa = next(p for p in parties if p["party_type"] == "contratado")
    assert pessoa["document_masked"] == "***.***.***-09"  # CPF


def test_timeline_do_caso(case_id):
    a = str(uuid.uuid4())
    events = _data(tl_h.list_timeline(_event(a, path={"caseId": case_id}), None))
    types = {e["event_type"] for e in events}
    assert {"request_created", "case_created", "party_added",
            "document_attached", "triage_plan_created", "wizard_completed"} <= types
    assert len(events) == 7  # 2 system + 2 party_added + doc + plan + wizard


def test_triagem_do_caso(case_id):
    a = str(uuid.uuid4())
    mods = _data(tr_h.list_triage(_event(a, path={"caseId": case_id}), None))
    assert len(mods) == 8  # plano de analise_contratual
    assert all(m["status"] == "not_started" for m in mods)
    assert all(m["provider"].startswith(("mock_", "mock")) for m in mods)


def test_get_case_enriquecido(case_id):
    a = str(uuid.uuid4())
    case = _data(cases_h.get_case(_event(a, path={"caseId": case_id}), None))
    assert case["product_type"] == "analise_contratual"
    assert case["status"] == "awaiting_triage"
    assert case["code"].startswith("REQ-")
    assert case["parties_count"] == 2
    assert case["documents_count"] == 1
    assert case["timeline_count"] == 7
    assert case["triage_count"] == 8
    assert case["pricing"]["total_price_cents"] == 10800


def test_isolamento_outra_org_404(case_id):
    b = str(uuid.uuid4())
    p = {"caseId": case_id}
    assert cp_h.list_case_parties(_event(b, path=p, org_id=OTHER_ORG), None)["statusCode"] == 404
    assert tl_h.list_timeline(_event(b, path=p, org_id=OTHER_ORG), None)["statusCode"] == 404
    assert tr_h.list_triage(_event(b, path=p, org_id=OTHER_ORG), None)["statusCode"] == 404
    assert cases_h.get_case(_event(b, path=p, org_id=OTHER_ORG), None)["statusCode"] == 404


def test_caso_inexistente_404(case_id):
    a = str(uuid.uuid4())
    p = {"caseId": str(uuid.uuid4())}
    assert cp_h.list_case_parties(_event(a, path=p), None)["statusCode"] == 404
    assert tl_h.list_timeline(_event(a, path=p), None)["statusCode"] == 404
    assert tr_h.list_triage(_event(a, path=p), None)["statusCode"] == 404


def test_viewer_pode_ler(case_id):
    v = str(uuid.uuid4())
    assert cp_h.list_case_parties(_event(v, role="viewer", path={"caseId": case_id}),
                                  None)["statusCode"] == 200
