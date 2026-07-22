# tests/test_payments_handler.py
from _dbadmin import admin_conn
import json
import uuid

import psycopg2
import pytest

from src.handlers import payments as pay_h
from src.handlers import pricing as pr_h
from src.handlers import reports as rep_h
from src.handlers import requests as req_h
from src.handlers import triage as tri_h
from src.schemas.pricing_schemas import PaymentSelectionSchema

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"


# ── Schema puro (sem DB): validadores de débito ──────────────────────────────

def test_schema_debito_aceita_1x_com_token():
    s = PaymentSelectionSchema(parcelas=1, method="debito", idempotency_key="k",
                               card_token="tok_x")
    assert s.method == "debito" and s.parcelas == 1


def test_schema_debito_parcelas_diferente_de_1_falha():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PaymentSelectionSchema(parcelas=2, method="debito", idempotency_key="k",
                               card_token="tok_x")


def test_schema_debito_sem_token_falha():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PaymentSelectionSchema(parcelas=1, method="debito", idempotency_key="k")


def _admin_conn():
    return admin_conn()


def _reset():
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.requests, public.cases, public.pricing_configs"
                    " RESTART IDENTITY CASCADE")
    conn.close()


def _seed_user(user_id, org=SYSTEM_ORG, role="admin", status="active"):
    """Semeia public.users — o PUT /pricing/config reconsulta o papel ATUAL no banco
    (assert_active_admin, SEC-02), então um user_id sintético seria recusado com 403."""
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s,%s,'x','Test',%s,%s,%s)"
            " ON CONFLICT (id) DO UPDATE SET role=EXCLUDED.role, status=EXCLUDED.status",
            (user_id, f"u_{user_id}@t.c", role, status, org))
    conn.close()


def _admin(org=SYSTEM_ORG):
    """user_id de um admin ATIVO e existente no banco."""
    uid = str(uuid.uuid4())
    _seed_user(uid, org=org, role="admin", status="active")
    return uid


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
    admin = _admin()
    # O retorno é conferido de propósito: um setup que falha calado (ex.: 403 do
    # assert_active_admin) só apareceria 40 linhas adiante como "parcelas não ofertadas".
    _cfg = pr_h.update_pricing_config(_event(admin, body={"installment_config": {
        "enabled": True, "max_parcelas": 6, "sem_juros_ate": 3, "juros_mensal_bps": 299,
        "valor_minimo_parcela_cents": 0, "primeiro_vencimento_dias": 30, "dia_vencimento": 10,
        "allowed_methods": {"cartao": {"enabled": True, "max_parcelas": 6}}}}), None)
    assert _cfg["statusCode"] in (200, 201), f"setup do parcelamento falhou: {_cfg}"
    resp = req_h.create_request(_event(admin, body={
        "product_type": "analise_contratual",
        "title": "Contrato de teste de pagamento",
        "parties": [{"name": "Empresa X LTDA", "role": "contratante", "person_type": "company"}],
        "selected_modules": ["ia_deepseek", "analise_contratual_ia"],
        "idempotency_key": str(uuid.uuid4()),
    }), None)
    return _data(resp)["case_id"], admin


def _insert_case_without_request():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.organization_id', %s, false)", (SYSTEM_ORG,))
        cur.execute(
            "INSERT INTO public.cases (organization_id, case_type, status, product_type,"
            " product_label, title) VALUES (%s, 'analise_contratual', 'open',"
            " 'analise_contratual', 'Análise', 'Caso sem pedido') RETURNING id",
            (SYSTEM_ORG,),
        )
        case_id = cur.fetchone()[0]
    conn.close()
    return str(case_id)


def test_pagamento_case_inexistente_retorna_404():
    admin = _admin()
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 1, "method": "pix", "idempotency_key": "missing-case"},
        path={"caseId": str(uuid.uuid4())}), None)
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 404, resp
    assert body["error"] == "Caso não encontrado"


def test_pagamento_case_sem_pedido_retorna_409():
    admin = _admin()
    case_id = _insert_case_without_request()
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 1, "method": "pix", "idempotency_key": "sem-pedido"},
        path={"caseId": case_id}), None)
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 409, resp
    assert "sem pedido" in body["error"]


def test_pagamento_grava_plano_e_status(seed_case_and_config):
    case_id, admin = seed_case_and_config  # caso pending + config habilitada
    body = {"parcelas": 3, "method": "cartao", "idempotency_key": "p1",
            "card_token": "tok_mock_x"}
    resp = pay_h.create_case_payment(_event(admin, body=body, path={"caseId": case_id}), None)
    data = _data(resp)
    assert data["payment_status"] == "simulated"
    assert data["installment_plan"]["parcelas"] == 3
    assert "raw" not in data["installment_plan"]["payment"]

def test_pagamento_expected_total_divergente_retorna_409_sem_cobrar(seed_case_and_config):
    # PRC-02: o total confirmado na tela (expected_total_cents) != o recalculado com a
    # config vigente -> 409 pedindo revisão, e NENHUMA cobrança emitida (request segue
    # pending, sem plano). Prova o consentimento por valor.
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 1, "method": "cartao", "idempotency_key": "prc02-diverge",
        "card_token": "tok_mock_x", "expected_total_cents": 9999},  # valor que a tela "viu", errado
        path={"caseId": case_id}), None)
    assert resp["statusCode"] == 409, resp
    conn = _admin_conn()  # dbadmin bypassa RLS
    with conn.cursor() as cur:
        cur.execute("SELECT payment_status, installment_plan FROM public.requests WHERE case_id=%s",
                    (case_id,))
        ps, plan = cur.fetchone()
    conn.close()
    assert ps == "pending" and plan is None  # nada foi cobrado


def test_pagamento_expected_total_correto_prossegue(seed_case_and_config):
    # PRC-02 não-regressão: expected batendo com o recalculado (1x = total, sem juros) -> segue.
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 1, "method": "cartao", "idempotency_key": "prc02-ok",
        "card_token": "tok_mock_x", "expected_total_cents": 2900 + 7900},
        path={"caseId": case_id}), None)
    assert resp["statusCode"] in (200, 201), resp


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


# ── Débito: cartão à vista (parcelas==1, mesma tokenização que crédito) ───────

def _enable_debito(admin):
    """Habilita débito (à vista) além de crédito na config da org."""
    pr_h.update_pricing_config(_event(admin, body={"installment_config": {
        "enabled": True, "max_parcelas": 6, "sem_juros_ate": 3, "juros_mensal_bps": 299,
        "valor_minimo_parcela_cents": 0, "primeiro_vencimento_dias": 30, "dia_vencimento": 10,
        "allowed_methods": {"cartao": {"enabled": True, "max_parcelas": 6},
                            "debito": {"enabled": True, "max_parcelas": 1}}}}), None)


def test_debito_a_vista_grava_plano(seed_case_and_config):
    case_id, admin = seed_case_and_config
    _enable_debito(admin)  # config precisa ofertar débito no 1x
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 1, "method": "debito", "idempotency_key": "d1",
        "card_token": "tok_mock_deb", "card_last4": "5151", "card_brand": "elo"},
        path={"caseId": case_id}), None)
    data = _data(resp)
    assert data["payment_status"] == "simulated"
    plan = data["installment_plan"]
    assert plan["parcelas"] == 1 and plan["method"] == "debito"
    assert plan["payment"]["payment_form"]["last4"] == "5151"
    # invariante PCI: nada de token/CVV/holder crus persistidos
    blob = json.dumps(plan)
    assert "tok_mock_deb" not in blob and "card_token" not in blob and "cvv" not in blob


def test_debito_exige_parcela_1_senao_400(seed_case_and_config):
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 3, "method": "debito", "idempotency_key": "d2",
        "card_token": "tok_mock_deb"}, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 400, resp


def test_debito_sem_token_400(seed_case_and_config):
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 1, "method": "debito", "idempotency_key": "d3"},
        path={"caseId": case_id}), None)
    assert resp["statusCode"] == 400, resp


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
    _seed_user(analyst, role="analyst", status="active")  # writer ativo no banco (SEC-01)
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


# ── SEC-10: reembolso não conta como pago (gate bloqueia novas operações) ──────

def _set_payment_status(case_id, status):
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE public.requests SET payment_status=%s WHERE case_id=%s",
                    (status, case_id))
    conn.close()


def test_sec10_reembolso_bloqueia_triagem_e_relatorio(seed_case_and_config, monkeypatch):
    case_id, admin = seed_case_and_config
    monkeypatch.setenv("PAYMENT_GATE", "hard")
    _set_payment_status(case_id, "refunded")  # allowlist _PAID_STATUSES não inclui refunded
    assert tri_h.run_triage(_event(admin, path={"caseId": case_id}), None)["statusCode"] == 402
    assert rep_h.generate_case_report(
        _event(admin, path={"caseId": case_id}), None)["statusCode"] == 402
    # contraprova: um estado pago (simulated) libera a triagem
    _set_payment_status(case_id, "simulated")
    assert tri_h.run_triage(_event(admin, path={"caseId": case_id}), None)["statusCode"] == 200


# ── _PAID_STATUSES env-sensível: 'simulated' (mock) não libera em produção ────

def test_gate_prod_simulated_nao_libera(seed_case_and_config, monkeypatch):
    # Em PRODUÇÃO, uma linha 'simulated' (resíduo de dados migrados de dev) NÃO pode
    # destravar trabalho pago — só 'paid' (dinheiro real). Defesa em profundidade
    # (mesmo espírito do PRC-01/A2: mock nunca tem efeito de dinheiro real fora de dev).
    case_id, admin = seed_case_and_config
    monkeypatch.setenv("PAYMENT_GATE", "hard")
    monkeypatch.setenv("ENVIRONMENT", "prod")
    _set_payment_status(case_id, "simulated")
    assert tri_h.run_triage(_event(admin, path={"caseId": case_id}), None)["statusCode"] == 402
    # contraprova: pagamento REAL libera
    _set_payment_status(case_id, "paid")
    assert tri_h.run_triage(_event(admin, path={"caseId": case_id}), None)["statusCode"] == 200


def test_gate_dev_simulated_ainda_libera(seed_case_and_config, monkeypatch):
    # Não-regressão: em dev/test (ENVIRONMENT local), 'simulated' AINDA libera o gate
    # hard — o mock precisa exercitar o fluxo pós-pagamento sem um gateway real.
    case_id, admin = seed_case_and_config
    monkeypatch.setenv("PAYMENT_GATE", "hard")
    monkeypatch.setenv("ENVIRONMENT", "local")
    _set_payment_status(case_id, "simulated")
    assert tri_h.run_triage(_event(admin, path={"caseId": case_id}), None)["statusCode"] == 200


# ── M4: rollback/finalize escopados à própria reserva (anti-clobber) ──────────

def test_m4_rollback_nao_reverte_reserva_de_concorrente(seed_case_and_config):
    """Uma tentativa A (reserved_at antigo) NÃO pode reverter/sequestrar a reserva
    RECÉM-criada por um concorrente B — o guard `AND updated_at = reserved_at` casa
    apenas a própria reserva."""
    case_id, _admin = seed_case_and_config
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        # A reservou (órfã, 10 min atrás) -> token t_a
        cur.execute("UPDATE public.requests SET payment_status='processing',"
                    " updated_at = now() - interval '10 minutes' WHERE case_id=%s"
                    " RETURNING updated_at", (case_id,))
        t_a = cur.fetchone()[0]
        # B reclama a órfã -> nova reserva com token t_b
        cur.execute("UPDATE public.requests SET payment_status='processing', updated_at=now()"
                    " WHERE case_id=%s AND payment_status='processing'"
                    " AND updated_at < now() - interval '5 minutes' RETURNING updated_at", (case_id,))
        t_b = cur.fetchone()[0]
        assert t_b != t_a
        # A (tardio) tenta o rollback com o token ANTIGO -> não toca a reserva de B
        cur.execute("UPDATE public.requests SET payment_status='pending', updated_at=now()"
                    " WHERE case_id=%s AND payment_status='processing' AND updated_at=%s",
                    (case_id, t_a))
        assert cur.rowcount == 0  # guard impediu o clobber
        cur.execute("SELECT payment_status, updated_at FROM public.requests WHERE case_id=%s",
                    (case_id,))
        st, ua = cur.fetchone()
        assert st == "processing" and ua == t_b  # reserva de B intacta
        # contraprova: com o token correto (t_b), o rollback reverte a própria reserva
        cur.execute("UPDATE public.requests SET payment_status='pending', updated_at=now()"
                    " WHERE case_id=%s AND payment_status='processing' AND updated_at=%s",
                    (case_id, t_b))
        assert cur.rowcount == 1
    conn.close()
