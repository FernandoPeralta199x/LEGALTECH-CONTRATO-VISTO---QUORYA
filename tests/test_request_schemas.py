"""Testes PUROS (sem DB) do schema de criação de pedido (SRC-02).

Provam que `source_mode` é um domínio fechado: o wizard só envia "local", o modo
misto "hybrid" é aceito, e qualquer outro valor (forjar procedência "real"/"mock"/
"simulated") OU uma string acima de varchar(32) é rejeitada pelo Pydantic ANTES do
banco — fechando de uma vez a forja de procedência e o 500 por StringDataRightTruncation.
"""
import pytest
from pydantic import ValidationError

from src.schemas.request_schemas import RequestCreateSchema


def _base(**over):
    payload = {"product_type": "ia_deepseek"}
    payload.update(over)
    return payload


def test_source_mode_default_local():
    assert RequestCreateSchema(**_base()).source_mode == "local"


@pytest.mark.parametrize("valido", ["local", "hybrid"])
def test_source_mode_aceita_dominio(valido):
    assert RequestCreateSchema(**_base(source_mode=valido)).source_mode == valido


@pytest.mark.parametrize("forjado", ["real", "mock", "simulated", "REAL", "prod"])
def test_source_mode_rejeita_forja_de_procedencia(forjado):
    # Antes do fix (str livre) isto persistia 'real'/'mock' e contaminava o selo de
    # procedência; agora o Literal recusa antes de tocar o banco.
    with pytest.raises(ValidationError):
        RequestCreateSchema(**_base(source_mode=forjado))


def test_source_mode_rejeita_string_longa_sem_500():
    # Antes do fix, > varchar(32) provocava StringDataRightTruncation capturado pelo
    # except genérico → error_response(500). Agora o Literal recusa (422/400) antes.
    with pytest.raises(ValidationError):
        RequestCreateSchema(**_base(source_mode="x" * 64))
