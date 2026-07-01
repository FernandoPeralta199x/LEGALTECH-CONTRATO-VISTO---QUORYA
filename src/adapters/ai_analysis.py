"""Adapter IA / LLM — análise jurídica por IA (serverless, síncrono).

- ``MockAIAnalysisAdapter``: análise fictícia para dev local.
- ``RealAIAnalysisAdapter``: placeholder para AWS Bedrock / OpenAI (impl. AWS).
  Default de provider = ``bedrock`` (decisão de arquitetura: usa créditos AWS,
  acessível por VPC endpoint, sem NAT).
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from src.adapters.base import AdapterResult

logger = logging.getLogger(__name__)


@runtime_checkable
class AIAnalysisPort(Protocol):
    """Interface pública do adapter de análise IA."""

    def analyze_contract(self, text: str, analysis_type: str = "general") -> AdapterResult:
        """Analisa texto contratual e retorna insights."""
        ...

    def generate_summary(self, text: str) -> AdapterResult:
        """Gera resumo executivo do documento."""
        ...

    def assess_risk(self, text: str) -> AdapterResult:
        """Avalia riscos identificados no texto."""
        ...


class MockAIAnalysisAdapter:
    """Implementação local com respostas fictícias."""

    def analyze_contract(self, text: str, analysis_type: str = "general") -> AdapterResult:
        logger.info("MockAI.analyze_contract type=%s chars=%d", analysis_type, len(text))
        return AdapterResult(
            success=True,
            source="mock",
            data={
                "analysis_type": analysis_type,
                "clauses_found": 8,
                "key_findings": [
                    "Clausula de rescisao unilateral sem penalidade — risco moderado",
                    "Prazo de vigencia compativel com mercado",
                    "Clausula de confidencialidade presente e adequada",
                    "Foro de eleicao: Comarca de Sao Paulo",
                ],
                "overall_risk": "medium",
                "confidence": 0.82,
            },
        )

    def generate_summary(self, text: str) -> AdapterResult:
        logger.info("MockAI.generate_summary chars=%d", len(text))
        return AdapterResult(
            success=True,
            source="mock",
            data={
                "summary": (
                    "Contrato de prestacao de servicos juridicos com prazo de 12 meses "
                    "e valor de R$ 120.000,00. Contem clausulas padrao de confidencialidade, "
                    "rescisao e foro de eleicao. Risco geral: moderado."
                ),
                "word_count": len(text.split()),
            },
        )

    def assess_risk(self, text: str) -> AdapterResult:
        logger.info("MockAI.assess_risk chars=%d", len(text))
        return AdapterResult(
            success=True,
            source="mock",
            data={
                "overall_risk": "medium",
                "risks": [
                    {
                        "category": "contractual",
                        "level": "medium",
                        "title": "Clausula de rescisao unilateral",
                        "description": "Permite rescisao sem penalidade com aviso previo de 30 dias.",
                    },
                    {
                        "category": "financial",
                        "level": "low",
                        "title": "Valor compativel com mercado",
                        "description": "Valor dentro da faixa esperada para o tipo de servico.",
                    },
                ],
                "confidence": 0.78,
            },
        )


class RealAIAnalysisAdapter:
    """Placeholder — implementação real na AWS (AWS Bedrock / OpenAI)."""

    def __init__(self, provider: str = "bedrock", api_key: str | None = None) -> None:
        self._provider = provider
        self._api_key = api_key

    def analyze_contract(self, text: str, analysis_type: str = "general") -> AdapterResult:
        raise NotImplementedError(
            f"RealAIAnalysisAdapter ({self._provider}) não implementado (impl. AWS).")

    def generate_summary(self, text: str) -> AdapterResult:
        raise NotImplementedError(
            f"RealAIAnalysisAdapter ({self._provider}) não implementado (impl. AWS).")

    def assess_risk(self, text: str) -> AdapterResult:
        raise NotImplementedError(
            f"RealAIAnalysisAdapter ({self._provider}) não implementado (impl. AWS).")


def create_ai_analysis_adapter(
    backend: str | None = None,
    provider: str | None = None,
    api_key: str | None = None,
) -> AIAnalysisPort:
    """Fábrica controlada por env: AI_ANALYSIS_BACKEND (mock|real),
    AI_ANALYSIS_PROVIDER (bedrock|openai), AI_ANALYSIS_API_KEY."""
    backend = backend or os.getenv("AI_ANALYSIS_BACKEND", "mock")
    provider = provider or os.getenv("AI_ANALYSIS_PROVIDER", "bedrock")
    api_key = api_key or os.getenv("AI_ANALYSIS_API_KEY")
    if backend == "real":
        return RealAIAnalysisAdapter(provider=provider, api_key=api_key)
    return MockAIAnalysisAdapter()
