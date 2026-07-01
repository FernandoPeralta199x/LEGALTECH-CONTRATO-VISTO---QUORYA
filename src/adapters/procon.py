"""Adapter Procon — reclamações de consumo / reputação (serverless, síncrono).

- ``MockProconAdapter``: dados fictícios para dev local.
- ``RealProconAdapter``: placeholder para integração real na AWS (requer API key /
  acesso ao consumidor.gov.br / base do Procon estadual).
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from src.adapters.base import AdapterResult

logger = logging.getLogger(__name__)


@runtime_checkable
class ProconPort(Protocol):
    """Interface pública do adapter Procon/reputação de consumo."""

    def check_complaints(self, cpf_cnpj: str) -> AdapterResult:
        """Consulta reclamações de consumo e índice de resolução."""
        ...


class MockProconAdapter:
    """Implementação local com dados fictícios."""

    def check_complaints(self, cpf_cnpj: str) -> AdapterResult:
        logger.info("MockProcon.check_complaints (documento omitido por LGPD)")
        return AdapterResult(
            success=True,
            source="mock",
            data={
                "cpf_cnpj": cpf_cnpj,
                "complaints": 0,
                "resolution_rate": 1.0,
                "reputation": "otima",
                "last_complaint_date": None,
            },
        )


class RealProconAdapter:
    """Placeholder — implementação real na AWS (requer PROCON_API_KEY)."""

    def __init__(self, api_key: str, base_url: str = "https://consumidor.gov.br") -> None:
        self._api_key = api_key
        self._base_url = base_url

    def check_complaints(self, cpf_cnpj: str) -> AdapterResult:
        raise NotImplementedError(
            "RealProconAdapter não implementado — aguardando contrato/API key (impl. AWS).")


def create_procon_adapter(backend: str | None = None, api_key: str | None = None) -> ProconPort:
    """Fábrica controlada por env: PROCON_BACKEND (mock|real), PROCON_API_KEY."""
    backend = backend or os.getenv("PROCON_BACKEND", "mock")
    api_key = api_key or os.getenv("PROCON_API_KEY")
    if backend == "real":
        if not api_key:
            raise ValueError("PROCON_API_KEY obrigatória para backend=real")
        return RealProconAdapter(api_key=api_key)
    return MockProconAdapter()
