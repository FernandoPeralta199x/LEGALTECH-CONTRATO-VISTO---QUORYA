"""Teste PURO (sem DB) do fail-closed de _serialize (SEC-08).

A função decide quanta PII sai na resposta (viewer vê documento mascarado e sem
contato/endereço/RG). O `role` é obrigatório: esquecer o argumento deve virar
TypeError aqui, nunca um vazamento silencioso de PII em produção.
"""
import pytest

from src.handlers.clients import _serialize

_ROW = {
    "id": "11111111-1111-4111-8111-111111111111",
    "legal_name": "Fulano de Tal",
    "document_type": "cpf",
    "document_number": "11144477735",
    "email": "fulano@example.test",
    "phone": "+55 11 99999-0000",
    "rg": "12.345.678-9",
    "address_street": "Rua A",
    "address_city": "São Paulo",
    "address_state": "SP",
    "address_zip": "01000-000",
    "status": "active",
    "created_at": None,
    "updated_at": None,
    "cases_count": 0,
}


def test_serialize_exige_role_sem_default():
    # SEC-08: sem `role` deve falhar RUIDOSAMENTE (TypeError), não devolver PII.
    with pytest.raises(TypeError):
        _serialize(_ROW)  # type: ignore[call-arg]


def test_serialize_viewer_reduz_pii():
    out = _serialize(_ROW, "viewer")
    assert out["email"] is None
    assert out["phone"] is None
    assert out["rg"] is None
    assert out["address"] is None
    assert out["document"] != "11144477735"  # mascarado


def test_serialize_admin_ve_pii_completa():
    out = _serialize(_ROW, "admin")
    assert out["email"] == "fulano@example.test"
    assert out["document"] == "11144477735"
