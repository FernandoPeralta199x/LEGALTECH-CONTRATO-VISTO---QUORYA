"""Adapter OCR / Textract — extração de texto de documentos (serverless, síncrono).

- ``MockOCRAdapter``: texto fictício para dev local.
- ``RealOCRAdapter``: placeholder para AWS Textract (impl. AWS, via VPC endpoint).
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from src.adapters.base import AdapterResult

logger = logging.getLogger(__name__)


@runtime_checkable
class OCRPort(Protocol):
    """Interface pública do adapter OCR."""

    def extract_text(self, file_path: str, mime_type: str = "application/pdf") -> AdapterResult:
        """Extrai texto de um documento."""
        ...


class MockOCRAdapter:
    """Implementação local com texto fictício."""

    def extract_text(self, file_path: str, mime_type: str = "application/pdf") -> AdapterResult:
        logger.info("MockOCR.extract_text path=%s mime=%s", file_path, mime_type)
        return AdapterResult(
            success=True,
            source="mock",
            data={
                "file_path": file_path,
                "mime_type": mime_type,
                "pages": 3,
                "text": (
                    "CONTRATO DE PRESTACAO DE SERVICOS\n\n"
                    "Clausula 1 - Do Objeto\n"
                    "O presente contrato tem por objeto a prestacao de servicos "
                    "juridicos de consultoria e assessoria.\n\n"
                    "Clausula 2 - Do Prazo\n"
                    "O prazo de vigencia e de 12 (doze) meses, contados da assinatura.\n\n"
                    "Clausula 3 - Do Valor\n"
                    "O valor total e de R$ 120.000,00 (cento e vinte mil reais).\n"
                ),
                "confidence": 0.95,
            },
        )


class RealOCRAdapter:
    """Placeholder — implementação real na AWS (AWS Textract)."""

    def __init__(self, aws_region: str = "sa-east-1") -> None:
        self._aws_region = aws_region

    def extract_text(self, file_path: str, mime_type: str = "application/pdf") -> AdapterResult:
        raise NotImplementedError(
            "RealOCRAdapter não implementado — aguardando config AWS Textract (impl. AWS).")


def create_ocr_adapter(backend: str | None = None, aws_region: str | None = None) -> OCRPort:
    """Fábrica controlada por env: OCR_BACKEND (mock|real), AWS_REGION."""
    backend = backend or os.getenv("OCR_BACKEND", "mock")
    aws_region = aws_region or os.getenv("AWS_REGION", "sa-east-1")
    if backend == "real":
        return RealOCRAdapter(aws_region=aws_region)
    return MockOCRAdapter()
