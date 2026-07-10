# tests/test_pix_handlers.py
"""Handlers Pix (create-charge / status / webhook / simulate) contra o banco de teste.
Cobre o caminho crítico de segurança: criar SÓ cria (PENDING_PAYMENT, nunca pago),
confirmação SÓ por webhook assinado, dedup, cross-check de valor, replay e RBAC."""
from _dbadmin import admin_conn
import json
import uuid

import pytest

from src.adapters.pix import create_pix_provider
from src.handlers import pix as pix_h
from src.handlers import pricing as pr_h
from src.handlers import requests as req_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"


def _admin_conn():
    return admin_conn()


def _reset():
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.pix_webhook_events, public.pix_charges,"
                    " public.requests, public.cases, public.pricing_configs"
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


def _pstatus(conn_amount_payment_id):
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT amount_cents FROM public.pix_charges WHERE payment_id=%s",
                    (conn_amount_payment_id,))
        amt = cur.fetchone()[0]
    conn.close()
    return amt


def _request_payment_status():
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT payment_status FROM public.requests LIMIT 1")
        st = cur.fetchone()[0]
    conn.close()
    return st


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def _seed(pix_enabled=True):
    """Caso pending + config (com ou sem Pix habilitado no 1x)."""
    admin = str(uuid.uuid4())
    methods = {"cartao": {"enabled": True, "max_parcelas": 6}}
    if pix_enabled:
        methods["pix"] = {"enabled": True, "max_parcelas": 1}
    pr_h.update_pricing_config(_event(admin, body={"installment_config": {
        "enabled": True, "max_parcelas": 6, "sem_juros_ate": 3, "juros_mensal_bps": 299,
        "valor_minimo_parcela_cents": 0, "primeiro_vencimento_dias": 30, "dia_vencimento": 10,
        "allowed_methods": methods}}), None)
    resp = req_h.create_request(_event(admin, body={
        "product_type": "analise_contratual", "title": "Contrato Pix teste",
        "parties": [{"name": "Empresa X LTDA", "role": "contratante", "person_type": "company"}],
        "selected_modules": ["ia_deepseek", "analise_contratual_ia"],
        "idempotency_key": str(uuid.uuid4())}), None)
    return _data(resp)["case_id"], admin


@pytest.fixture
def seed():
    return _seed()


def _create(case_id, admin, key="pix-1"):
    return pix_h.create_pix_charge(
        _event(admin, body={"idempotency_key": key}, path={"caseId": case_id}), None)


def _status(case_id, admin):
    return _data(pix_h.get_pix_status(_event(admin, path={"caseId": case_id}), None))


def _signed(payment_id, amount_cents, status="PAID", currency="BRL", event_id=None):
    payload = json.dumps({"event_id": event_id or f"ev_{uuid.uuid4().hex}",
                          "payment_id": payment_id, "status": status,
                          "amount_cents": amount_cents, "currency": currency}).encode()
    return payload, create_pix_provider().sign(payload)


def _webhook(payload, signature, provider="mock"):
    return pix_h.pix_webhook({"pathParameters": {"provider": provider},
                              "headers": {"x-pix-signature": signature},
                              "body": payload.decode()}, None)


# ─────────────────────────── createPixCharge ───────────────────────────

def test_create_charge_pending_com_6_campos(seed):
    case_id, admin = seed
    data = _data(_create(case_id, admin))
    assert data["status"] == "PENDING_PAYMENT"
    assert set(data.keys()) == {"orderId", "paymentId", "qrCodeImage",
                                "pixCopyPaste", "expiresAt", "status"}
    assert data["paymentId"].startswith("mock_pix_")
    assert data["pixCopyPaste"] and "MOCK-PIX" in data["pixCopyPaste"]


def test_create_nunca_marca_pago(seed):
    case_id, admin = seed
    _data(_create(case_id, admin))
    assert _status(case_id, admin)["status"] == "PENDING_PAYMENT"
    assert _request_payment_status() == "pending"  # o pedido NÃO fica pago ao criar a cobrança


def test_case_inexistente_404(seed):
    _case_id, admin = seed
    resp = pix_h.create_pix_charge(_event(admin, body={"idempotency_key": "x"},
                                          path={"caseId": str(uuid.uuid4())}), None)
    assert resp["statusCode"] == 404


def test_replay_devolve_mesma_cobranca(seed):
    case_id, admin = seed
    r1 = _data(_create(case_id, admin, key="rep"))
    r2 = _data(_create(case_id, admin, key="rep"))
    assert r1["paymentId"] == r2["paymentId"]  # replay: mesma cobrança


def test_segunda_cobranca_ativa_409(seed):
    case_id, admin = seed
    _data(_create(case_id, admin, key="k1"))
    resp = _create(case_id, admin, key="k2")  # chave diferente + cobrança já ativa
    assert resp["statusCode"] == 409


def test_pix_desabilitado_400():
    case_id, admin = _seed(pix_enabled=False)  # config só com cartão
    resp = _create(case_id, admin)
    assert resp["statusCode"] == 400


def test_viewer_403(seed):
    case_id, _admin = seed
    viewer = str(uuid.uuid4())
    resp = pix_h.create_pix_charge(_event(viewer, role="viewer", body={"idempotency_key": "v"},
                                          path={"caseId": case_id}), None)
    assert resp["statusCode"] == 403


# ─────────────────────────── pixWebhook ───────────────────────────

def test_webhook_assinatura_invalida_401(seed):
    case_id, admin = seed
    charge = _data(_create(case_id, admin))
    payload, _sig = _signed(charge["paymentId"], 0)
    assert _webhook(payload, "assinatura-errada")["statusCode"] == 401
    # e a cobrança segue PENDING (não tocou o banco)
    assert _status(case_id, admin)["status"] == "PENDING_PAYMENT"


def test_webhook_confirma_e_projeta_paid(seed):
    case_id, admin = seed
    charge = _data(_create(case_id, admin))
    amount = _pstatus(charge["paymentId"])
    payload, sig = _signed(charge["paymentId"], amount)
    assert _webhook(payload, sig)["statusCode"] == 200
    assert _status(case_id, admin)["status"] == "PAID"          # cobrança confirmada
    assert _request_payment_status() == "paid"                  # projetado no pedido


def test_webhook_dedup(seed):
    case_id, admin = seed
    charge = _data(_create(case_id, admin))
    amount = _pstatus(charge["paymentId"])
    payload, sig = _signed(charge["paymentId"], amount, event_id="dup-1")
    r1 = _webhook(payload, sig)
    r2 = _webhook(payload, sig)  # mesmo event_id 2x
    assert r1["statusCode"] == 200 and r2["statusCode"] == 200
    # o 2o webhook do mesmo evento NÃO reprocessa (dedup do event_id OU transição já-aplicada):
    d2 = json.loads(r2["body"])["data"]
    assert d2.get("deduped") or d2.get("ignored")
    assert _status(case_id, admin)["status"] == "PAID"  # pago uma única vez
    assert _request_payment_status() == "paid"


def test_webhook_divergencia_de_valor_nao_paga(seed):
    case_id, admin = seed
    charge = _data(_create(case_id, admin))
    payload, sig = _signed(charge["paymentId"], 999999)  # valor errado, porém ASSINADO
    resp = _webhook(payload, sig)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["data"].get("ignored") is True
    assert _status(case_id, admin)["status"] == "PENDING_PAYMENT"  # NÃO marcou pago


def test_webhook_cobranca_desconhecida_ignora():
    payload, sig = _signed("mock_pix_inexistente", 100)
    resp = _webhook(payload, sig)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["data"].get("ignored") is True


# ─────────────────────────── simulate (dev) ───────────────────────────

def test_simulate_confirma_via_webhook(seed):
    case_id, admin = seed
    _data(_create(case_id, admin))
    resp = pix_h.simulate_pix_paid(_event(admin, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 200
    assert _status(case_id, admin)["status"] == "PAID"
    assert _request_payment_status() == "paid"


def test_simulate_sem_cobranca_404(seed):
    case_id, admin = seed
    resp = pix_h.simulate_pix_paid(_event(admin, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 404  # não há cobrança ativa para simular
