# tests/test_payments_handler.py
import json
import uuid

import psycopg2
import pytest

from src.handlers import payments as pay_h
from src.handlers import pricing as pr_h
from src.handlers import requests as req_h
from src.handlers import triage as tri_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"


def _admin_conn():
    return psycopg2.connect(host="localhost", port=5433, user="dbadmin",
                            password="localdev_cv", dbname="contrato_visto", connect_timeout=5)


def _reset():
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.requests, public.cases, public.pricing_configs"
                    " RESTART IDENTITY CASCADE")
    conn.close()


def _event(user_id, role="admin", body=None, path=None, org_id=SYSTEM_ORG):
    return {"requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                              "role": role, "organization_id": org_id}},
            "body": json.dumps(body) if body is not None else None,
            "pathParameters": path or {}, "queryStringParameters": {}}


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


@pytest.fixture
def seed_case_and_config():
    """Caso pending + parcelamento habilitado (6x, 3 sem juros, cartão até 6x)."""
    admin = str(uuid.uuid4())
    pr_h.update_pricing_config(_event(admin, body={"installment_config": {
        "enabled": True, "max_parcelas": 6, "sem_juros_ate": 3, "juros_mensal_bps": 299,
        "valor_minimo_parcela_cents": 0, "primeiro_vencimento_dias": 30, "dia_vencimento": 10,
        "allowed_methods": {"cartao": {"enabled": True, "max_parcelas": 6}}}}), None)
    resp = req_h.create_request(_event(admin, body={
        "product_type": "analise_contratual",
        "title": "Contrato de teste de pagamento",
        "parties": [{"name": "Empresa X LTDA", "role": "contratante", "person_type": "company"}],
        "selected_modules": ["ia_deepseek", "analise_contratual_ia"],
        "idempotency_key": str(uuid.uuid4()),
    }), None)
    return _data(resp)["case_id"], admin

def test_pagamento_grava_plano_e_status(seed_case_and_config):
    case_id, admin = seed_case_and_config  # caso pending + config habilitada
    body = {"parcelas": 3, "method": "cartao", "idempotency_key": "p1"}
    resp = pay_h.create_case_payment(_event(admin, body=body, path={"caseId": case_id}), None)
    data = _data(resp)
    assert data["payment_status"] == "simulated"
    assert data["installment_plan"]["parcelas"] == 3
    assert "raw" not in data["installment_plan"]["payment"]

def test_pagamento_rejeita_parcela_nao_ofertada(seed_case_and_config):
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(
        _event(admin, body={"parcelas": 99, "method": "cartao", "idempotency_key": "p2"},
               path={"caseId": case_id}), None)
    assert resp["statusCode"] == 400

def test_pagamento_idempotente_replay(seed_case_and_config):
    case_id, admin = seed_case_and_config
    b = {"parcelas": 3, "method": "cartao", "idempotency_key": "p3"}
    r1 = _data(pay_h.create_case_payment(_event(admin, body=b, path={"caseId": case_id}), None))
    r2 = _data(pay_h.create_case_payment(_event(admin, body=b, path={"caseId": case_id}), None))
    assert r1["installment_plan"]["payment"]["external_reference"] == \
           r2["installment_plan"]["payment"]["external_reference"]

def test_pagamento_ja_pago_rejeita_payload_diferente(seed_case_and_config):
    case_id, admin = seed_case_and_config
    _data(pay_h.create_case_payment(_event(admin, body={"parcelas": 3, "method": "cartao",
          "idempotency_key": "p4"}, path={"caseId": case_id}), None))
    resp = pay_h.create_case_payment(_event(admin, body={"parcelas": 6, "method": "cartao",
          "idempotency_key": "p4"}, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 409


# ── Gate de pagamento na triagem (PAYMENT_GATE=hard) ──────────────────────────

def _pay(case_id, admin, parcelas=1, key="gate-pay"):
    return pay_h.create_case_payment(
        _event(admin, body={"parcelas": parcelas, "method": "cartao",
                            "idempotency_key": key}, path={"caseId": case_id}), None)


def test_gate_hard_bloqueia_triagem_sem_pagamento(seed_case_and_config, monkeypatch):
    case_id, admin = seed_case_and_config
    monkeypatch.setenv("PAYMENT_GATE", "hard")
    resp = tri_h.run_triage(_event(admin, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 402, resp
    # após pagar, libera
    assert _pay(case_id, admin)["statusCode"] == 201
    liberado = tri_h.run_triage(_event(admin, path={"caseId": case_id}), None)
    assert liberado["statusCode"] == 200, liberado


def test_gate_soft_permite_triagem_sem_pagamento(seed_case_and_config, monkeypatch):
    case_id, admin = seed_case_and_config
    monkeypatch.delenv("PAYMENT_GATE", raising=False)  # default = soft
    resp = tri_h.run_triage(_event(admin, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 200, resp
