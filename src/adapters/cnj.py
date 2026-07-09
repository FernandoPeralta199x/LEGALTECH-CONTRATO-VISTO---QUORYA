"""Adapter CNJ / Tribunal — consulta processual direta (serverless, síncrono).

- ``MockCNJAdapter``: dados fictícios para dev local.
- ``RealCNJAdapter``: placeholder para DataJud / APIs dos tribunais (impl. AWS).
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from src.adapters.base import AdapterResult

logger = logging.getLogger(__name__)


@runtime_checkable
class CNJPort(Protocol):
    """Interface pública do adapter CNJ/Tribunal."""

    def search_by_number(self, process_number: str) -> AdapterResult:
        """Consulta processo por número unificado."""
        ...

    def search_by_party(self, name: str, document: str | None = None) -> AdapterResult:
        """Consulta processos por nome/documento de parte."""
        ...


class MockCNJAdapter:
    """Implementação local com dados fictícios."""

    def search_by_number(self, process_number: str) -> AdapterResult:
        logger.info("MockCNJ.search_by_number number_len=%d", len(process_number))  # sem PII no log (be-sec-05)
        return AdapterResult(
            success=True,
            source="mock",
            data={
                "process_number": process_number,
                "court": "TJSP",
                "class": "Procedimento Comum Civel",
                "subject": "Contratos Bancarios",
                "judge": "Juiz Mock da Silva",
                "filing_date": "2024-03-15",
                "status": "Em andamento",
                "last_movement": {
                    "date": "2024-12-01",
                    "description": "Conclusos para despacho",
                },
            },
        )

    def search_by_party(self, name: str, document: str | None = None) -> AdapterResult:
        logger.info("MockCNJ.search_by_party name_len=%d", len(name))  # sem PII no log (be-sec-05)
        return AdapterResult(
            success=True,
            source="mock",
            data={
                "name": name,
                "total": 1,
                "processes": [
                    {
                        "number": "0005555-12.2024.8.26.0100",
                        "court": "TJSP",
                        "role": "Autor",
                        "status": "Ativo",
                    },
                ],
            },
        )


class RealCNJAdapter:
    """Placeholder — implementação real na AWS (requer acesso DataJud/tribunal)."""

    def __init__(self, api_key: str, base_url: str = "https://api-publica.datajud.cnj.jus.br") -> None:
        self._api_key = api_key
        self._base_url = base_url

    def search_by_number(self, process_number: str) -> AdapterResult:
        raise NotImplementedError(
            "RealCNJAdapter não implementado — aguardando acesso DataJud (impl. AWS).")

    def search_by_party(self, name: str, document: str | None = None) -> AdapterResult:
        raise NotImplementedError(
            "RealCNJAdapter não implementado — aguardando acesso DataJud (impl. AWS).")


def create_cnj_adapter(backend: str | None = None, api_key: str | None = None) -> CNJPort:
    """Fábrica controlada por env: CNJ_BACKEND (mock|real), CNJ_API_KEY."""
    backend = backend or os.getenv("CNJ_BACKEND", "mock")
    api_key = api_key or os.getenv("CNJ_API_KEY")
    if backend == "real":
        if not api_key:
            raise ValueError("CNJ_API_KEY obrigatória para backend=real")
        return RealCNJAdapter(api_key=api_key)
    return MockCNJAdapter()
