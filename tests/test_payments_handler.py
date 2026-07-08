# tests/test_payments_handler.py
from _dbadmin import admin_conn
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
    return admin_conn()


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
    body = {"parcelas": 3, "method": "cartao", "idempotency_key": "p1",
            "card_token": "tok_mock_x"}
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
    b = {"parcelas": 3, "method": "cartao", "idempotency_key": "p3",
         "card_token": "tok_mock_x"}
    r1 = _data(pay_h.create_case_payment(_event(admin, body=b, path={"caseId": case_id}), None))
    r2 = _data(pay_h.create_case_payment(_event(admin, body=b, path={"caseId": case_id}), None))
    assert r1["installment_plan"]["payment"]["external_reference"] == \
           r2["installment_plan"]["payment"]["external_reference"]

def test_pagamento_ja_pago_rejeita_payload_diferente(seed_case_and_config):
    case_id, admin = seed_case_and_config
    _data(pay_h.create_case_payment(_event(admin, body={"parcelas": 3, "method": "cartao",
          "idempotency_key": "p4", "card_token": "tok_mock_x"}, path={"caseId": case_id}), None))
    resp = pay_h.create_case_payment(_event(admin, body={"parcelas": 6, "method": "cartao",
          "idempotency_key": "p4", "card_token": "tok_mock_x"}, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 409


# ── Cartão via token: sem token → 400, campo cru → 400, grava last4 sem sensível ──

def test_cartao_sem_token_400(seed_case_and_config):
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 3, "method": "cartao", "idempotency_key": "c1"}, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 400

def test_campo_cru_de_cartao_e_rejeitado_400(seed_case_and_config):
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 3, "method": "cartao", "idempotency_key": "c2",
        "card_token": "tok_mock_1", "card_number": "4111111111111111"},
        path={"caseId": case_id}), None)
    assert resp["statusCode"] == 400  # extra=forbid

def test_cartao_grava_last4_sem_dado_sensivel(seed_case_and_config):
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 3, "method": "cartao", "idempotency_key": "c3",
        "card_token": "tok_mock_1", "card_last4": "4242", "card_brand": "visa",
        "card_holder": "Fulano de Tal"},
        path={"caseId": case_id}), None)
    data = _data(resp)
    pay = data["installment_plan"]["payment"]
    blob = json.dumps(data["installment_plan"])
    assert pay["payment_form"]["last4"] == "4242"
    assert "card_token" not in blob and "tok_mock_1" not in blob and "cvv" not in blob
    assert "holder" not in blob and "Fulano" not in blob


# ── Gate de pagamento na triagem (PAYMENT_GATE=hard) ──────────────────────────

def _pay(case_id, admin, parcelas=1, key="gate-pay"):
    return pay_h.create_case_payment(
        _event(admin, body={"parcelas": parcelas, "method": "cartao",
                            "idempotency_key": key, "card_token": "tok_mock_x"},
               path={"caseId": case_id}), None)


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


def test_viewer_nao_paga_403(seed_case_and_config):
    case_id, _admin = seed_case_and_config
    viewer = str(uuid.uuid4())
    resp = pay_h.create_case_payment(_event(viewer, role="viewer", body={
        "parcelas": 1, "method": "pix", "idempotency_key": "vw-1"},
        path={"caseId": case_id}), None)
    assert resp["statusCode"] == 403, resp


def test_analyst_pode_pagar(seed_case_and_config):
    case_id, _admin = seed_case_and_config
    analyst = str(uuid.uuid4())
    resp = pay_h.create_case_payment(_event(analyst, role="analyst", body={
        "parcelas": 1, "method": "cartao", "idempotency_key": "an-1",
        "card_token": "tok_mock_an"}, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 201, resp


# ── Recuperação de reserva 'processing' travada (processo morreu no meio) ──────

def _set_processing(case_id, minutes_ago):
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public.requests SET payment_status='processing',"
            " updated_at = now() - (%s || ' minutes')::interval WHERE case_id = %s",
            (str(minutes_ago), case_id))
    conn.close()


def test_processing_travado_e_recuperavel_apos_timeout(seed_case_and_config):
    case_id, admin = seed_case_and_config
    _set_processing(case_id, 10)  # reserva órfã de 10 min atrás (processo morreu)
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 1, "method": "cartao", "idempotency_key": "rec-1",
        "card_token": "tok_mock_rec"}, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 201, resp


def test_processing_recente_bloqueia_concorrencia(seed_case_and_config):
    case_id, admin = seed_case_and_config
    _set_processing(case_id, 0)  # concorrente acabou de reservar
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 1, "method": "cartao", "idempotency_key": "rec-2",
        "card_token": "tok_mock_rec2"}, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 409, resp
