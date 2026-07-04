# tests/test_payment_adapter.py
import os
import pytest
from src.adapters.payment import (PaymentRequest, MockPaymentProvider, RealPaymentProvider,
                                   create_payment_provider)

def _req(method="cartao"):
    return PaymentRequest(amount_cents=10000, installments=3, method=method,
                          case_reference="case-1", organization_id="org-1",
                          idempotency_key="k1", schedule=[{"numero": 1, "valor_cents": 3334}])

@pytest.mark.parametrize("method", ["pix", "boleto", "cartao"])
def test_mock_retorna_simulated_por_metodo(method):
    res = MockPaymentProvider().create_charge(_req(method))
    assert res.status == "simulated" and res.method == method
    assert res.external_reference and res.external_reference.startswith("mock_")
    pub = res.to_public()
    assert "raw" not in pub and pub["status"] == "simulated"

def test_factory_default_mock():
    prov = create_payment_provider()
    assert isinstance(prov, MockPaymentProvider)

def test_real_placeholder_falha_claro():
    prov = RealPaymentProvider(provider="pagarme", mode="sandbox", api_key="x")
    with pytest.raises(NotImplementedError):
        prov.create_charge(_req())

def test_factory_real_exige_api_key(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROVIDER", "pagarme")
    monkeypatch.setenv("PAYMENT_MODE", "sandbox")
    monkeypatch.delenv("PAYMENT_API_KEY", raising=False)
    with pytest.raises(ValueError):
        create_payment_provider()


# ── Task 2 (plano 2026-07-04-cartao-tokenizacao): to_public allowlist + hints de cartão ──

def test_to_public_allowlist_cartao_remove_desconhecidos():
    from src.adapters.payment import PaymentResult
    r = PaymentResult(provider="mock", mode="mock", status="simulated", method="cartao",
                      external_reference="mock_x",
                      payment_form={"type": "cartao", "brand": "visa", "last4": "1234",
                                    "authorization_code": "A", "simulated": True,
                                    "card_number": "4111111111111111", "cvv": "123",
                                    "token": "tok_secret", "cpf": "060..."})
    pub = r.to_public()
    keys = set(pub["payment_form"].keys())
    assert keys <= {"type", "brand", "last4", "authorization_code", "simulated"}
    assert "card_number" not in keys and "cvv" not in keys and "token" not in keys


def test_mock_cartao_usa_hints_e_marca_simulated():
    from src.adapters.payment import PaymentRequest, MockPaymentProvider
    req = PaymentRequest(amount_cents=10000, installments=3, method="cartao",
                         case_reference="c1", organization_id="o1", idempotency_key="k",
                         schedule=[], card_token="tok_mock_1", card_last4_hint="4242",
                         card_brand_hint="visa")
    res = MockPaymentProvider().create_charge(req)
    pub = res.to_public()["payment_form"]
    assert pub["last4"] == "4242" and pub["brand"] == "visa" and pub["simulated"] is True
