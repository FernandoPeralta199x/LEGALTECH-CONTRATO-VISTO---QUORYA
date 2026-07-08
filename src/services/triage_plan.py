"""Plano de triagem por produto (serverless) — portado de
``legaltech-aws/apps/api/src/modules/triage/service.py``.

Define os módulos técnicos de triagem de cada produto. Cada módulo aponta para um
``provider`` (mock_*) que mapeia 1:1 nos adapters externos (``src/adapters``).

REGRA DE EXECUÇÃO POR SELEÇÃO (correção 2026-07-08): a triagem só pode executar
o que foi COMPRADO. Cada módulo técnico declara ``billing_module`` — o código do
módulo COMERCIAL (``src/services/pricing/config.py:MODULES``) que o habilita:

- ``billing_module=None``  => infraestrutura básica do produto (sempre entra);
- ``billing_module="x"``   => só entra se ``"x"`` estiver nos módulos selecionados
  normalizados (``normalize_selected_modules``, que já força os obrigatórios —
  CVS-008), mantendo execução consistente com a cobrança.

Use ``plan_for_selection(product, selected)`` ao criar ``triage_modules`` (é o que
o wizard/``create_request`` faz). ``plan_for_product`` retorna o roteiro COMPLETO
do produto (catálogo) e NÃO deve ser usado para decidir execução.

O ``required`` do módulo técnico é semântica de exibição/roteiro (ordem, selo),
não de billing: um técnico ``required=True`` cujo ``billing_module`` não foi
comprado NÃO roda (ex.: ``serasa`` em ``dados_partes`` sem ``serasa_procon``).

Módulos comerciais sem módulo técnico de execução: ``revisao_humana`` e
``reuniao_equipe`` (etapas HUMANAS pós-triagem, sem conector — por design). Todos
os demais comerciais têm ao menos um módulo técnico que roda quando comprados
(``targetdata`` foi ligado em 2026-07-08 — ver ADR-001).
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
    # Módulo COMERCIAL que habilita este módulo técnico (None = infra do produto).
    billing_module: str | None = None


TRIAGE_PLANS: dict[str, list[TriageModuleDefinition]] = {
    "dados_partes": [
        TriageModuleDefinition("parties_validation", "Validação das partes", "mock_local", True, "Produto depende de dados das partes.", None),
        TriageModuleDefinition("serasa", "Consulta Serasa", "mock_serasa", True, "Avaliar sinais cadastrais e comerciais das partes em modo mock.", "serasa_procon"),
        TriageModuleDefinition("procon", "Consulta Procon", "mock_procon", False, "Avaliar ocorrências de consumo em modo mock.", "serasa_procon"),
        TriageModuleDefinition("escavador", "Consulta Escavador", "mock_escavador", False, "Avaliar litígios públicos em modo mock.", "escavador"),
        TriageModuleDefinition("targetdata", "Enriquecimento cadastral (TargetData)", "mock_targetdata", True, "Dados cadastrais/comerciais das partes (enriquecimento).", "targetdata"),
        TriageModuleDefinition("reputation_summary", "Resumo reputacional", "mock_ai_summary", False, "Consolidar sinais simulados das partes.", "ia_deepseek"),
        TriageModuleDefinition("ai_summary", "Resumo IA", "mock_ai_summary", False, "Gerar resumo local/simulado sem valor jurídico real.", "ia_deepseek"),
    ],
    "consulta_objeto": [
        TriageModuleDefinition("object_analysis", "Análise do objeto", "mock_document_parser", True, "Produto depende de análise inicial do objeto.", None),
        TriageModuleDefinition("public_search", "Busca pública simulada", "mock_escavador", False, "Simular busca pública sem chamada externa.", "escavador"),
        TriageModuleDefinition("targetdata", "Enriquecimento cadastral (TargetData)", "mock_targetdata", False, "Enriquecimento cadastral do objeto/partes quando contratado.", "targetdata"),
        TriageModuleDefinition("document_summary", "Resumo documental", "mock_document_parser", False, "Simular extração de pontos relevantes.", "ia_deepseek"),
        TriageModuleDefinition("ai_summary", "Resumo IA", "mock_ai_summary", False, "Gerar resumo local/simulado sem valor jurídico real.", "ia_deepseek"),
    ],
    "analise_contratual": [
        TriageModuleDefinition("document_parser", "Parser documental", "mock_document_parser", True, "Análise contratual depende da leitura do documento.", None),
        TriageModuleDefinition("ocr", "OCR", "mock_ocr", False, "OCR simulado para documentos que exijam extração textual.", None),
        TriageModuleDefinition("contract_risk_analysis", "Análise de risco contratual", "mock_ai_summary", True, "Simular identificação de riscos contratuais.", "analise_contratual_ia"),
        TriageModuleDefinition("obligations_mapping", "Mapeamento de obrigações", "mock_ai_summary", False, "Simular extração de obrigações do contrato.", "analise_contratual_ia"),
        TriageModuleDefinition("serasa", "Consulta Serasa", "mock_serasa", False, "Simular análise cadastral das partes relacionadas.", "serasa_procon"),
        TriageModuleDefinition("procon", "Consulta Procon", "mock_procon", False, "Simular análise de ocorrências de consumo.", "serasa_procon"),
        TriageModuleDefinition("escavador", "Consulta Escavador", "mock_escavador", False, "Simular consulta de litígios públicos.", "escavador"),
        TriageModuleDefinition("targetdata", "Enriquecimento cadastral (TargetData)", "mock_targetdata", False, "Enriquecimento cadastral das partes do contrato quando contratado.", "targetdata"),
        TriageModuleDefinition("ai_report", "Pré-relatório IA", "mock_ai_report", False, "Preparar pré-relatório simulado para etapa futura.", "ia_deepseek"),
    ],
    "reuniao_advogado": [
        TriageModuleDefinition("preliminary_questions", "Perguntas preliminares", "mock_ai_summary", True, "Preparar questões para reunião jurídica.", "reuniao_equipe"),
        TriageModuleDefinition("documents_checklist", "Checklist documental", "mock_document_parser", True, "Simular checklist de documentos necessários.", "reuniao_equipe"),
        TriageModuleDefinition("targetdata", "Enriquecimento cadastral (TargetData)", "mock_targetdata", False, "Enriquecimento cadastral para o briefing quando contratado.", "targetdata"),
        TriageModuleDefinition("case_summary", "Resumo do caso", "mock_ai_summary", False, "Gerar resumo local/simulado para briefing.", "ia_deepseek"),
        TriageModuleDefinition("lawyer_briefing", "Briefing para advogado", "mock_ai_summary", False, "Preparar briefing simulado para revisão humana.", "revisao_humana"),
        TriageModuleDefinition("ai_briefing", "Briefing IA", "mock_ai_summary", False, "Gerar apoio simulado sem decisão jurídica real.", "ia_deepseek"),
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
    TriageModuleDefinition("case_summary", "Resumo do caso", "mock_ai_summary", True, "Produto sem mapeamento específico usa triagem mínima simulada.", None),
    TriageModuleDefinition("ai_summary", "Resumo IA", "mock_ai_summary", False, "Resumo local/simulado para validação de fluxo.", "ia_deepseek"),
]


def plan_for_product(product_type: str) -> list[TriageModuleDefinition]:
    """Roteiro COMPLETO de triagem do produto (catálogo). NÃO usar para decidir
    execução — para isso, ``plan_for_selection``."""
    normalized = PRODUCT_ALIASES.get(product_type, product_type)
    return TRIAGE_PLANS.get(normalized, DEFAULT_TRIAGE_PLAN)


def plan_for_selection(product_type: str, selected_modules) -> list[TriageModuleDefinition]:
    """Plano de triagem EXECUTÁVEL: infra básica do produto + módulos técnicos
    habilitados pelos módulos comerciais selecionados (já normalizados por
    ``normalize_selected_modules`` — obrigatórios sempre presentes, CVS-008).

    É a única forma correta de gerar ``triage_modules``: garante que a triagem
    não consome conectores/APIs que o cliente não comprou."""
    selected = set(selected_modules or [])
    return [d for d in plan_for_product(product_type)
            if d.billing_module is None or d.billing_module in selected]
