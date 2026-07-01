"""Coração — testes da camada de adapters externos (mock + factory + Real placeholder).

Não tocam o banco: validam o contrato dos adapters (formato legaltech-aws, serverless).
"""
import pytest

from src.adapters.base import AdapterResult
from src.adapters.escavador import (
    EscavadorPort, MockEscavadorAdapter, create_escavador_adapter,
)
from src.adapters.serasa import create_serasa_adapter
from src.adapters.cnj import create_cnj_adapter
from src.adapters.ocr import create_ocr_adapter
from src.adapters.ai_analysis import create_ai_analysis_adapter
from src.adapters.procon import (
    ProconPort, MockProconAdapter, create_procon_adapter,
)
from src.adapters.targetdata import (
    TargetDataPort, MockTargetDataAdapter, create_targetdata_adapter,
)


def test_escavador_mock():
    a = create_escavador_adapter("mock")
    assert isinstance(a, EscavadorPort)  # Protocol runtime_checkable
    r = a.search_lawsuits("12345678900")
    assert isinstance(r, AdapterResult) and r.success and r.source == "mock"
    assert r.data["total"] == 2
    assert a.get_lawsuit_details("mock-proc-001").success


def test_escavador_real_requires_key_and_is_placeholder():
    with pytest.raises(ValueError):
        create_escavador_adapter("real", api_key=None)
    real = create_escavador_adapter("real", api_key="k")
    with pytest.raises(NotImplementedError):
        real.search_lawsuits("x")


def test_serasa_mock_and_real():
    assert create_serasa_adapter("mock").check_credit("123").data["score"] == 720
    with pytest.raises(ValueError):
        create_serasa_adapter("real", api_key=None)


def test_cnj_mock_and_real():
    a = create_cnj_adapter("mock")
    assert a.search_by_number("0005555-12.2024.8.26.0100").success
    assert a.search_by_party("Fulano").data["total"] == 1
    with pytest.raises(ValueError):
        create_cnj_adapter("real", api_key=None)


def test_ocr_mock_and_real():
    r = create_ocr_adapter("mock").extract_text("/tmp/x.pdf")
    assert r.success and r.data["pages"] == 3 and "CONTRATO" in r.data["text"]
    with pytest.raises(NotImplementedError):
        create_ocr_adapter("real").extract_text("/tmp/x.pdf")


def test_ai_mock_and_real():
    a = create_ai_analysis_adapter("mock")
    assert a.analyze_contract("texto").data["overall_risk"] == "medium"
    assert a.generate_summary("a b c").data["word_count"] == 3
    assert a.assess_risk("texto").success
    with pytest.raises(NotImplementedError):
        create_ai_analysis_adapter("real", provider="bedrock").generate_summary("x")


def test_procon_mock_and_real():
    a = create_procon_adapter("mock")
    assert isinstance(a, ProconPort)  # Protocol runtime_checkable
    r = a.check_complaints("12345678900")
    assert isinstance(r, AdapterResult) and r.success and r.source == "mock"
    assert r.data["complaints"] == 0 and r.data["resolution_rate"] == 1.0
    with pytest.raises(ValueError):
        create_procon_adapter("real", api_key=None)
    real = create_procon_adapter("real", api_key="k")
    with pytest.raises(NotImplementedError):
        real.check_complaints("x")


def test_targetdata_mock_and_real():
    a = create_targetdata_adapter("mock")
    assert isinstance(a, TargetDataPort)  # Protocol runtime_checkable
    r = a.lookup("12345678900")
    assert isinstance(r, AdapterResult) and r.success and r.source == "mock"
    assert r.data["found"] is True and r.data["registration_status"] == "regular"
    with pytest.raises(ValueError):
        create_targetdata_adapter("real", api_key=None)
    real = create_targetdata_adapter("real", api_key="k")
    with pytest.raises(NotImplementedError):
        real.lookup("x")


def test_factory_defaults_to_mock_from_env(monkeypatch):
    monkeypatch.delenv("ESCAVADOR_BACKEND", raising=False)
    assert isinstance(create_escavador_adapter(), MockEscavadorAdapter)
    monkeypatch.delenv("PROCON_BACKEND", raising=False)
    assert isinstance(create_procon_adapter(), MockProconAdapter)
    monkeypatch.delenv("TARGETDATA_BACKEND", raising=False)
    assert isinstance(create_targetdata_adapter(), MockTargetDataAdapter)
