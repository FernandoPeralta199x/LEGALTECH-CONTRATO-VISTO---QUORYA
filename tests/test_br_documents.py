"""B7 — validação de documentos BR (CPF/CNPJ numérico e CNPJ ALFANUMÉRICO 2026).
Sem banco: exercita `src/schemas/br_documents.py` diretamente."""
import pytest

from src.schemas.br_documents import (
    clean_document,
    infer_document_type,
    is_valid_cnpj,
    is_valid_cpf,
    validate_document,
)


def test_clean_document_remove_pontuacao_e_uppercase():
    assert clean_document("123.456.789-09") == "12345678909"
    assert clean_document("12.abc.345/01de-35") == "12ABC34501DE35"


@pytest.mark.parametrize("cpf", ["12345678909", "98765432100"])
def test_cpf_valido(cpf):
    assert is_valid_cpf(cpf)


@pytest.mark.parametrize("cpf", ["12345678900", "11111111111", "1234567890", "123456789099"])
def test_cpf_invalido(cpf):
    assert not is_valid_cpf(cpf)


@pytest.mark.parametrize("cnpj", ["11222333000181"])
def test_cnpj_numerico_valido(cnpj):
    assert is_valid_cnpj(cnpj)


@pytest.mark.parametrize("cnpj", ["12345678000199", "00000000000000", "1122233300018"])
def test_cnpj_numerico_invalido(cnpj):
    assert not is_valid_cnpj(cnpj)


def test_cnpj_alfanumerico_2026_valido():
    # Exemplo oficial RFB do CNPJ alfanumérico: 12.ABC.345/01DE-35
    assert is_valid_cnpj("12ABC34501DE35")


def test_cnpj_alfanumerico_2026_invalido_dv():
    # mesmo base, DV errado
    assert not is_valid_cnpj("12ABC34501DE34")


def test_cnpj_alfanumerico_rejeita_caractere_fora_do_conjunto():
    # "@" não pertence a [0-9A-Z]; DV também não pode ser alfabético
    assert not is_valid_cnpj("12ABC34501D@35")
    assert not is_valid_cnpj("12ABC34501DEA5")


def test_infer_document_type():
    assert infer_document_type("12345678909") == "cpf"
    assert infer_document_type("12ABC34501DE35") == "cnpj"
    assert infer_document_type("123") is None


def test_validate_document_ok_e_normaliza():
    assert validate_document("123.456.789-09") == ("12345678909", "cpf")
    assert validate_document("12.ABC.345/01DE-35") == ("12ABC34501DE35", "cnpj")
    # tipo declarado coerente
    assert validate_document("11.222.333/0001-81", "cnpj") == ("11222333000181", "cnpj")


def test_validate_document_incoerente_com_tipo():
    with pytest.raises(ValueError, match="incompatível"):
        validate_document("12345678909", "cnpj")  # 11 dígitos declarado como cnpj


def test_validate_document_dv_invalido():
    with pytest.raises(ValueError, match="dígito verificador"):
        validate_document("12345678000199")  # CNPJ com DV errado


def test_validate_document_tamanho_invalido():
    with pytest.raises(ValueError, match="11 .CPF. ou 14"):
        validate_document("123")
