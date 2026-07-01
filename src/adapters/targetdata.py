"""Adapter TargetData — enriquecimento cadastral (serverless, síncrono).

- ``MockTargetDataAdapter``: dados fictícios para dev local.
- ``RealTargetDataAdapter``: placeholder para integração real na AWS (requer API key).
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from src.adapters.base import AdapterResult

logger = logging.getLogger(__name__)


@runtime_checkable
class TargetDataPort(Protocol):
    """Interface pública do adapter TargetData/enriquecimento cadastral."""

    def lookup(self, cpf_cnpj: str) -> AdapterResult:
        """Enriquece dados cadastrais por CPF/CNPJ."""
        ...


class MockTargetDataAdapter:
    """Implementação local com dados fictícios."""

    def lookup(self, cpf_cnpj: str) -> AdapterResult:
        logger.info("MockTargetData.lookup (documento omitido por LGPD)")
        return AdapterResult(
            success=True,
            source="mock",
            data={
                "cpf_cnpj": cpf_cnpj,
                "registration_status": "regular",
                "person_type": "individual",
                "found": True,
                "aliases": [],
                "economic_group": None,
                "last_update": "2024-10-01",
            },
        )


class RealTargetDataAdapter:
    """Placeholder — implementação real na AWS (requer TARGETDATA_API_KEY)."""

    def __init__(self, api_key: str, base_url: str = "https://api.targetdata.com.br") -> None:
        self._api_key = api_key
        self._base_url = base_url

    def lookup(self, cpf_cnpj: str) -> AdapterResult:
        raise NotImplementedError(
            "RealTargetDataAdapter não implementado — aguardando contrato/API key (impl. AWS).")


def create_targetdata_adapter(backend: str | None = None, api_key: str | None = None) -> TargetDataPort:
    """Fábrica controlada por env: TARGETDATA_BACKEND (mock|real), TARGETDATA_API_KEY."""
    backend = backend or os.getenv("TARGETDATA_BACKEND", "mock")
    api_key = api_key or os.getenv("TARGETDATA_API_KEY")
    if backend == "real":
        if not api_key:
            raise ValueError("TARGETDATA_API_KEY obrigatória para backend=real")
        return RealTargetDataAdapter(api_key=api_key)
    return MockTargetDataAdapter()
