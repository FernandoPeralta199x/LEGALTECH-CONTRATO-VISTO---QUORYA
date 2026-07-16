"""Testes do registry central provider → adapter (sem banco).

Valida a canonicalização dos providers da triagem, a resolução para o adapter
Mock e o comportamento de "flip para real" (o único ponto de wiring)."""
import logging

import pytest

from src.adapters.base import AdapterResult
from src.adapters.registry import (
    canonical_provider_key,
    query_provider,
    resolve_binding,
    PROVIDER_BINDINGS,
)


@pytest.mark.parametrize("provider,expected", [
    ("mock_serasa", "serasa"),
    ("real_serasa", "serasa"),
    ("serasa", "serasa"),
    ("mock_procon", "procon"),
    ("mock_escavador", "escavador"),
    ("public_search", "escavador"),
    ("mock_targetdata", "targetdata"),
    ("mock_ai_summary", "ai"),
    ("mock_ai_report", "ai"),
    ("reputation_summary", "ai"),
    ("mock_document_parser", "ocr"),
    ("mock_ocr", "ocr"),
    ("mock_local", "local"),
    ("parties_validation", "local"),
    ("desconhecido_xyz", "desconhecido_xyz"),
])
def test_canonical_provider_key(provider, expected):
    assert canonical_provider_key(provider) == expected


def test_query_provider_mock_devolve_adapter_result():
    for provider in ("mock_serasa", "mock_procon", "mock_escavador",
                     "mock_targetdata", "mock_ai_summary", "mock_document_parser"):
        r = query_provider(provider, {"cpf_cnpj": "123", "text": "x", "file_path": "/tmp/x.pdf"})
        assert isinstance(r, AdapterResult)
        assert r.success and r.source == "mock"


def test_query_provider_local_ou_desconhecido_retorna_none():
    assert query_provider("mock_local") is None
    assert query_provider("parties_validation") is None
    assert query_provider("provider_inexistente") is None


def test_todos_bindings_cobrem_as_chaves_canonicas():
    # cada binding.key deve bater com a própria chave do dict (consistência)
    for key, binding in PROVIDER_BINDINGS.items():
        assert binding.key == key
    assert resolve_binding("mock_serasa") is PROVIDER_BINDINGS["serasa"]


def test_flip_para_real_exige_api_key(monkeypatch):
    """Trocar SERASA_BACKEND=real (sem key) faz o registry propagar ValueError —
    prova que o wiring real passa por um único ponto (as fábricas + env)."""
    monkeypatch.setenv("SERASA_BACKEND", "real")
    monkeypatch.delenv("SERASA_API_KEY", raising=False)
    with pytest.raises(ValueError):
        query_provider("mock_serasa", {"cpf_cnpj": "123"})


def test_provider_desconhecido_loga_aviso(caplog):
    """M1: um provider sem binding (typo/wiring ausente) vira no-op — deve logar
    aviso para não degradar silenciosamente."""
    with caplog.at_level(logging.WARNING, logger="src.adapters.registry"):
        assert query_provider("provider_inexistente") is None
    assert any(
        "provider_inexistente" in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    )


def test_provider_local_nao_loga_aviso(caplog):
    """'local'/'parties_validation' têm None ESPERADO (sem adapter externo) —
    não devem gerar aviso."""
    with caplog.at_level(logging.WARNING, logger="src.adapters.registry"):
        assert query_provider("parties_validation") is None
        assert query_provider("mock_local") is None
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
