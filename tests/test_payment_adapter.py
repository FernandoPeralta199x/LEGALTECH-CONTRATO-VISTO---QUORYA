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
