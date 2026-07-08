"""Testes PUROS do plano de triagem por seleção (sem banco).

Regra de negócio (bug corrigido em 2026-07-08): a triagem só pode executar os
módulos técnicos habilitados pelos módulos comerciais SELECIONADOS no wizard
(mais a infraestrutura básica do produto, ``billing_module=None``). Antes, o
plano era FIXO por produto e rodava conectores não comprados.
"""
import pytest

from src.services.pricing.config import MATRIX, MODULES
from src.services.pricing.estimate import normalize_selected_modules
from src.services.triage_plan import (
    TRIAGE_PLANS,
    plan_for_product,
    plan_for_selection,
)


def _keys(plan):
    return [d.module_key for d in plan]


def test_selecao_minima_nao_roda_conectores_nao_comprados():
    """analise_contratual só com os obrigatórios (ia_deepseek + analise_contratual_ia):
    NÃO pode gerar serasa/procon/escavador (módulos comerciais não comprados)."""
    selected = normalize_selected_modules(
        "analise_contratual", ["ia_deepseek", "analise_contratual_ia"])
    plan = plan_for_selection("analise_contratual", selected)
    keys = _keys(plan)
    assert "serasa" not in keys
    assert "procon" not in keys
    assert "escavador" not in keys
    # infraestrutura básica do produto + módulos dos comerciais comprados
    assert keys == ["document_parser", "ocr", "contract_risk_analysis",
                    "obligations_mapping", "ai_report"]


def test_serasa_procon_comprado_habilita_serasa_e_procon():
    selected = normalize_selected_modules(
        "analise_contratual",
        ["ia_deepseek", "analise_contratual_ia", "serasa_procon"])
    keys = _keys(plan_for_selection("analise_contratual", selected))
    assert "serasa" in keys
    assert "procon" in keys
    assert "escavador" not in keys  # continua não comprado


def test_escavador_comprado_habilita_escavador():
    selected = normalize_selected_modules(
        "analise_contratual",
        ["ia_deepseek", "analise_contratual_ia", "escavador"])
    keys = _keys(plan_for_selection("analise_contratual", selected))
    assert "escavador" in keys
    assert "serasa" not in keys
    assert "procon" not in keys


def test_selecao_completa_equivale_ao_plano_do_produto():
    """Comprando todos os módulos comerciais do produto, o plano executado é o
    roteiro completo (comportamento antigo vira caso-limite)."""
    todos = list(MATRIX["analise_contratual"].keys())
    selected = normalize_selected_modules("analise_contratual", todos)
    assert _keys(plan_for_selection("analise_contratual", selected)) == \
        _keys(plan_for_product("analise_contratual"))


def test_dados_partes_sem_serasa_procon_nao_roda_serasa_nem_procon():
    """No produto dados_partes, serasa é required=True no plano TÉCNICO, mas o
    módulo comercial serasa_procon é opcional: sem compra, não roda."""
    selected = normalize_selected_modules("dados_partes", [])  # baseline
    keys = _keys(plan_for_selection("dados_partes", selected))
    # baseline de dados_partes inclui serasa_procon? Só se default/required.
    # Na MATRIX, serasa_procon é recommended (não default): baseline NÃO inclui.
    assert "serasa" not in keys
    assert "procon" not in keys
    assert "parties_validation" in keys  # infra básica sempre presente
    assert "escavador" in keys  # escavador é required comercial em dados_partes


def test_alias_de_produto_e_respeitado():
    selected = normalize_selected_modules(
        "analise_contratual", ["ia_deepseek", "analise_contratual_ia"])
    assert _keys(plan_for_selection("contract_analysis", selected)) == \
        _keys(plan_for_selection("analise_contratual", selected))


def test_billing_modules_do_plano_existem_no_catalogo_comercial():
    """Todo billing_module apontado por um módulo técnico precisa existir no
    catálogo comercial (MODULES) — protege contra typo no mapeamento."""
    for product, plan in TRIAGE_PLANS.items():
        for d in plan:
            if d.billing_module is not None:
                assert d.billing_module in MODULES, (
                    f"{product}.{d.module_key} aponta para módulo comercial "
                    f"inexistente: {d.billing_module}")


def test_infra_basica_sempre_presente_mesmo_sem_selecao():
    for product in TRIAGE_PLANS:
        plan = plan_for_selection(product, [])
        infra = [d.module_key for d in TRIAGE_PLANS[product]
                 if d.billing_module is None]
        assert _keys(plan) == infra
