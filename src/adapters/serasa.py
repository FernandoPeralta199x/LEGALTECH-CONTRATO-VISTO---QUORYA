"""Adapter Serasa / bureau de crédito — score e restritivos (serverless, síncrono).

- ``MockSerasaAdapter``: dados fictícios para dev local.
- ``RealSerasaAdapter``: placeholder para integração real na AWS.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from src.adapters.base import AdapterResult

logger = logging.getLogger(__name__)


@runtime_checkable
class SerasaPort(Protocol):
    """Interface pública do adapter Serasa/bureau."""

    def check_credit(self, cpf_cnpj: str) -> AdapterResult:
        """Consulta score de crédito e restritivos."""
        ...


class MockSerasaAdapter:
    """Implementação local com dados fictícios."""

    def check_credit(self, cpf_cnpj: str) -> AdapterResult:
        logger.info("MockSerasa.check_credit (documento omitido por LGPD)")
        return AdapterResult(
            success=True,
            source="mock",
            data={
                "cpf_cnpj": cpf_cnpj,
                "score": 720,
                "score_range": "bom",
                "pendencies": 0,
                "protests": 0,
                "bounced_checks": 0,
                "last_query_date": "2024-11-20",
            },
        )


class RealSerasaAdapter:
    """Placeholder — implementação real na AWS (requer credenciais Serasa)."""

    def __init__(self, api_key: str, base_url: str = "https://api.serasaexperian.com.br") -> None:
        self._api_key = api_key
        self._base_url = base_url

    def check_credit(self, cpf_cnpj: str) -> AdapterResult:
        raise NotImplementedError(
            "RealSerasaAdapter não implementado — aguardando contrato/API key (impl. AWS).")


def create_serasa_adapter(backend: str | None = None, api_key: str | None = None) -> SerasaPort:
    """Fábrica controlada por env: SERASA_BACKEND (mock|real), SERASA_API_KEY."""
    backend = backend or os.getenv("SERASA_BACKEND", "mock")
    api_key = api_key or os.getenv("SERASA_API_KEY")
    if backend == "real":
        if not api_key:
            raise ValueError("SERASA_API_KEY obrigatória para backend=real")
        return RealSerasaAdapter(api_key=api_key)
    return MockSerasaAdapter()
