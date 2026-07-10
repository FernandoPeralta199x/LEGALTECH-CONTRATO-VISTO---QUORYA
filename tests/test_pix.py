"""Testes puros do módulo Pix: máquina de estados + seam PixProvider/MockPixProvider.
Sem banco, sem rede — roda isolado (mesmo estilo dos testes de installments)."""
import json

import pytest

from src.adapters.pix import (
    DEFAULT_PIX_TTL_SECONDS,
    MockPixProvider,
    PixCharge,
    PixOrder,
    PixSignatureError,
    PixWebhookEvent,
    RealPixProvider,
    create_pix_provider,
)
from src.services.pix import states


# ─────────────────────────── máquina de estados ───────────────────────────

def test_transicoes_validas_de_pending():
    for target in ("PAID", "EXPIRED", "PAYMENT_FAILED", "CANCELLED"):
        assert states.is_valid_transition("PENDING_PAYMENT", target)


def test_paid_so_vai_para_refunded():
    assert states.is_valid_transition("PAID", "REFUNDED")
    for target in ("PENDING_PAYMENT", "EXPIRED", "CANCELLED", "PAYMENT_FAILED", "PAID"):
        assert not states.is_valid_transition("PAID", target)


def test_terminais_nao_transicionam_in_place():
    for term in ("EXPIRED", "CANCELLED", "PAYMENT_FAILED", "REFUNDED"):
        assert states.is_terminal(term)
        for target in states.ALL_STATES:
            assert not states.is_valid_transition(term, target)


def test_assert_transition_levanta_em_transicao_invalida():
    states.assert_transition("PENDING_PAYMENT", "PAID")  # não levanta
    with pytest.raises(states.InvalidPixTransition):
        states.assert_transition("PENDING_PAYMENT", "REFUNDED")
    with pytest.raises(states.InvalidPixTransition):
        states.assert_transition("PAID", "PENDING_PAYMENT")


def test_retryable_e_paid_states():
    assert states.can_retry("EXPIRED") and states.can_retry("PAYMENT_FAILED")
    assert states.can_retry("CANCELLED")
    assert not states.can_retry("PENDING_PAYMENT") and not states.can_retry("PAID")
    # Só PAID libera trabalho (diferente do mock atual que aceitava "simulated").
    assert states.PAID_STATES == frozenset({"PAID"})


# ─────────────────────────── seam / MockPixProvider ───────────────────────────

def _order(amount_cents=91900):
    return PixOrder(amount_cents=amount_cents, case_reference="case-abc",
                    organization_id="org-1", idempotency_key="idem-1")


def test_create_dynamic_charge_devolve_pending_com_campos():
    charge = MockPixProvider().create_dynamic_charge(_order())
    assert charge.status == "PENDING_PAYMENT"
    assert charge.payment_id.startswith("mock_pix_case-abc_")
    assert "MOCK-PIX" in charge.pix_copy_paste and "919.00" in charge.pix_copy_paste
    assert charge.qr_code_image is None  # mock não gera imagem; FE renderiza do copia-e-cola
    assert charge.expires_at.endswith("+00:00")  # ISO-8601 UTC


def test_charge_unica_por_chamada():
    p = MockPixProvider()
    a = p.create_dynamic_charge(_order())
    b = p.create_dynamic_charge(_order())
    assert a.payment_id != b.payment_id  # cada cobrança tem paymentId único


def test_to_public_expoe_apenas_seis_campos():
    charge = MockPixProvider().create_dynamic_charge(_order())
    pub = charge.to_public(order_id="order-9")
    assert set(pub.keys()) == {"orderId", "paymentId", "qrCodeImage", "pixCopyPaste",
                               "expiresAt", "status"}
    assert pub["orderId"] == "order-9"


def test_webhook_assinatura_valida_normaliza_evento():
    p = MockPixProvider(webhook_secret="s3cr3t")
    payload = json.dumps({"event_id": "ev-1", "payment_id": "mock_pix_x",
                          "status": "PAID", "amount_cents": 91900}).encode()
    sig = p.sign(payload)
    ev = p.handle_webhook(payload, sig)
    assert isinstance(ev, PixWebhookEvent)
    assert (ev.event_id, ev.payment_id, ev.status, ev.amount_cents, ev.currency) == (
        "ev-1", "mock_pix_x", "PAID", 91900, "BRL")


def test_webhook_assinatura_invalida_e_rejeitada():
    p = MockPixProvider(webhook_secret="s3cr3t")
    payload = json.dumps({"event_id": "ev-1", "payment_id": "x",
                          "status": "PAID", "amount_cents": 100}).encode()
    with pytest.raises(PixSignatureError):
        p.handle_webhook(payload, "assinatura-errada")
    with pytest.raises(PixSignatureError):
        p.handle_webhook(payload, "")


def test_webhook_payload_adulterado_falha_assinatura():
    p = MockPixProvider(webhook_secret="s3cr3t")
    original = json.dumps({"event_id": "ev-1", "payment_id": "x",
                           "status": "PAID", "amount_cents": 100}).encode()
    sig = p.sign(original)
    tampered = json.dumps({"event_id": "ev-1", "payment_id": "x",
                           "status": "PAID", "amount_cents": 999999}).encode()
    with pytest.raises(PixSignatureError):
        p.handle_webhook(tampered, sig)  # assinatura não bate após adulteração do valor


def test_get_status_mock_devolve_pending():
    ev = MockPixProvider().get_status("mock_pix_x")
    assert ev.status == "PENDING_PAYMENT" and ev.payment_id == "mock_pix_x"


def test_factory_default_e_mock():
    assert isinstance(create_pix_provider(), MockPixProvider)
    assert isinstance(create_pix_provider(provider="mock"), MockPixProvider)


def test_factory_real_sem_api_key_levanta():
    with pytest.raises(ValueError):
        create_pix_provider(provider="asaas", mode="sandbox", api_key=None)


def test_factory_real_com_api_key_retorna_placeholder():
    prov = create_pix_provider(provider="asaas", mode="sandbox", api_key="k",
                               webhook_secret="w")
    assert isinstance(prov, RealPixProvider)
    with pytest.raises(NotImplementedError):
        prov.create_dynamic_charge(_order())


def test_ttl_default_15min():
    assert DEFAULT_PIX_TTL_SECONDS == 15 * 60


# ─────────────────────── fail-closed em produção (A1) ───────────────────────

def test_is_productive_environment(monkeypatch):
    from src.utils import safety
    for env in ("", "local", "dev", "development", "test", "testing"):
        monkeypatch.setenv("ENVIRONMENT", env)
        assert safety.is_productive_environment() is False
    for env in ("prod", "production", "staging", "homolog", "qa", "sandbox"):
        monkeypatch.setenv("ENVIRONMENT", env)
        assert safety.is_productive_environment() is True


def test_factory_bloqueia_mock_em_producao(monkeypatch):
    # Fail-closed: em prod, PIX_PROVIDER ausente/"mock" NÃO pode devolver o mock (segredo público).
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("PIX_PROVIDER", raising=False)
    monkeypatch.delenv("PAYMENT_MODE", raising=False)
    with pytest.raises(RuntimeError):
        create_pix_provider()
    # Mesmo com PAYMENT_MODE=live, PIX_PROVIDER=mock explícito segue bloqueado.
    monkeypatch.setenv("PAYMENT_MODE", "live")
    monkeypatch.setenv("PIX_PROVIDER", "mock")
    with pytest.raises(RuntimeError):
        create_pix_provider()


def test_factory_prod_com_provider_real_nao_e_mock(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PIX_PROVIDER", "asaas")
    monkeypatch.setenv("PAYMENT_MODE", "live")
    prov = create_pix_provider(api_key="k", webhook_secret="s" * 40)
    assert isinstance(prov, RealPixProvider)  # nunca cai no mock em produção


def test_enforce_production_safety_exige_pix_provider_e_secret(monkeypatch):
    from src.utils import safety
    # Ambiente produtivo com TODAS as outras travas satisfeitas — só o Pix inseguro.
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "j" * 40)
    monkeypatch.setenv("AI_ANALYSIS_BACKEND", "real")
    monkeypatch.setenv("EMAIL_BACKEND", "ses")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "real")
    monkeypatch.setenv("OCR_BACKEND", "real")
    monkeypatch.setenv("PAYMENT_PROVIDER", "asaas")
    monkeypatch.setenv("PAYMENT_MODE", "live")
    monkeypatch.setenv("PAYMENT_API_KEY", "k")
    monkeypatch.delenv("PIX_PROVIDER", raising=False)        # mock => viola
    monkeypatch.delenv("PIX_WEBHOOK_SECRET", raising=False)  # ausente => viola
    with pytest.raises(RuntimeError) as ei:
        safety.enforce_production_safety()
    msg = str(ei.value)
    assert "PIX_PROVIDER" in msg and "PIX_WEBHOOK_SECRET" in msg
    # O segredo default público também é rejeitado.
    monkeypatch.setenv("PIX_PROVIDER", "asaas")
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", "dev-pix-mock-secret")
    with pytest.raises(RuntimeError):
        safety.enforce_production_safety()
    # Segredo curto (< 32 chars) é rejeitado.
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", "curto")
    with pytest.raises(RuntimeError):
        safety.enforce_production_safety()
    # Config Pix segura (provider real + segredo forte) => NÃO levanta.
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", "s" * 40)
    safety.enforce_production_safety()
