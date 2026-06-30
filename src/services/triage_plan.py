"""Plano de triagem por produto (serverless) — portado de
``legaltech-aws/apps/api/src/modules/triage/service.py``.

Define os módulos técnicos de triagem gerados para cada produto (FIXO por produto).
Cada módulo aponta para um ``provider`` (mock_*) que mapeia 1:1 nos adapters externos
(``src/adapters``). A EXECUÇÃO dos módulos (rodar providers) é o SP3 (futuro);
aqui só geramos o PLANO (status NOT_STARTED).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriageModuleDefinition:
    module_key: str
    module_label: str
    provider: str
    required: bool
    reason: str


TRIAGE_PLANS: dict[str, list[TriageModuleDefinition]] = {
    "dados_partes": [
        TriageModuleDefinition("parties_validation", "Validação das partes", "mock_local", True, "Produto depende de dados das partes."),
        TriageModuleDefinition("serasa", "Consulta Serasa", "mock_serasa", True, "Avaliar sinais cadastrais e comerciais das partes em modo mock."),
        TriageModuleDefinition("procon", "Consulta Procon", "mock_procon", False, "Avaliar ocorrências de consumo em modo mock."),
        TriageModuleDefinition("escavador", "Consulta Escavador", "mock_escavador", False, "Avaliar litígios públicos em modo mock."),
        TriageModuleDefinition("reputation_summary", "Resumo reputacional", "mock_ai_summary", False, "Consolidar sinais simulados das partes."),
        TriageModuleDefinition("ai_summary", "Resumo IA", "mock_ai_summary", False, "Gerar resumo local/simulado sem valor jurídico real."),
    ],
    "consulta_objeto": [
        TriageModuleDefinition("object_analysis", "Análise do objeto", "mock_document_parser", True, "Produto depende de análise inicial do objeto."),
        TriageModuleDefinition("public_search", "Busca pública simulada", "mock_escavador", False, "Simular busca pública sem chamada externa."),
        TriageModuleDefinition("document_summary", "Resumo documental", "mock_document_parser", False, "Simular extração de pontos relevantes."),
        TriageModuleDefinition("ai_summary", "Resumo IA", "mock_ai_summary", False, "Gerar resumo local/simulado sem valor jurídico real."),
    ],
    "analise_contratual": [
        TriageModuleDefinition("document_parser", "Parser documental", "mock_document_parser", True, "Análise contratual depende da leitura do documento."),
        TriageModuleDefinition("ocr", "OCR", "mock_ocr", False, "OCR simulado para documentos que exijam extração textual."),
        TriageModuleDefinition("contract_risk_analysis", "Análise de risco contratual", "mock_ai_summary", True, "Simular identificação de riscos contratuais."),
        TriageModuleDefinition("obligations_mapping", "Mapeamento de obrigações", "mock_ai_summary", False, "Simular extração de obrigações do contrato."),
        TriageModuleDefinition("serasa", "Consulta Serasa", "mock_serasa", False, "Simular análise cadastral das partes relacionadas."),
        TriageModuleDefinition("procon", "Consulta Procon", "mock_procon", False, "Simular análise de ocorrências de consumo."),
        TriageModuleDefinition("escavador", "Consulta Escavador", "mock_escavador", False, "Simular consulta de litígios públicos."),
        TriageModuleDefinition("ai_report", "Pré-relatório IA", "mock_ai_report", False, "Preparar pré-relatório simulado para etapa futura."),
    ],
    "reuniao_advogado": [
        TriageModuleDefinition("preliminary_questions", "Perguntas preliminares", "mock_ai_summary", True, "Preparar questões para reunião jurídica."),
        TriageModuleDefinition("documents_checklist", "Checklist documental", "mock_document_parser", True, "Simular checklist de documentos necessários."),
        TriageModuleDefinition("case_summary", "Resumo do caso", "mock_ai_summary", False, "Gerar resumo local/simulado para briefing."),
        TriageModuleDefinition("lawyer_briefing", "Briefing para advogado", "mock_ai_summary", False, "Preparar briefing simulado para revisão humana."),
        TriageModuleDefinition("ai_briefing", "Briefing IA", "mock_ai_summary", False, "Gerar apoio simulado sem decisão jurídica real."),
    ],
}

PRODUCT_ALIASES = {
    "dados_das_partes": "dados_partes",
    "party_data": "dados_partes",
    "object_query": "consulta_objeto",
    "contract_analysis": "analise_contratual",
    "reuniao_com_advogado": "reuniao_advogado",
    "reuniao_equipe": "reuniao_advogado",
    "lawyer_meeting": "reuniao_advogado",
}

DEFAULT_TRIAGE_PLAN = [
    TriageModuleDefinition("case_summary", "Resumo do caso", "mock_ai_summary", True, "Produto sem mapeamento específico usa triagem mínima simulada."),
    TriageModuleDefinition("ai_summary", "Resumo IA", "mock_ai_summary", False, "Resumo local/simulado para validação de fluxo."),
]


def plan_for_product(product_type: str) -> list[TriageModuleDefinition]:
    """Retorna o plano de triagem (lista de módulos técnicos) para o produto."""
    normalized = PRODUCT_ALIASES.get(product_type, product_type)
    return TRIAGE_PLANS.get(normalized, DEFAULT_TRIAGE_PLAN)
