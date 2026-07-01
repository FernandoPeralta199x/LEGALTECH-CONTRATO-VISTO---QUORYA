"""Coração — testes do catálogo de pricing + estimativa (com overrides)."""
import pytest

from src.services.pricing import config as pc
from src.services.pricing.estimate import (
    effective_module_price_cents,
    estimate,
    normalize_selected_modules,
)


def test_catalogo_4_produtos_7_modulos():
    assert len(pc.PRODUCTS) == 4
    assert len(pc.MODULES) == 7


def test_preco_produto_e_soma_dos_modulos_required():
    # dados_partes: escavador+targetdata+ia = 6000+3900+2900
    assert pc.compute_product_base_price("dados_partes") == 12800
    # analise_contratual: ia+analise_contratual_ia = 2900+7900
    assert pc.compute_product_base_price("analise_contratual") == 10800
    # consulta_objeto: ia = 2900
    assert pc.compute_product_base_price("consulta_objeto") == 2900
    # reuniao_equipe: começa em 0
    assert pc.compute_product_base_price("reuniao_equipe") == 0


def test_estimate_base_mais_modulos_e_sla():
    # analise_contratual: base required = ia_deepseek+analise_contratual_ia = 10800;
    # revisao_humana é não-required selecionado -> entra em modules_total (12900).
    est = estimate("analise_contratual",
                   ["ia_deepseek", "analise_contratual_ia", "revisao_humana"])
    assert est["base_price_cents"] == 2900 + 7900  # 10800
    assert est["modules_total_cents"] == 12900
    assert est["total_price_cents"] == 23700
    assert est["sla_hours"] == 48 + 24  # produto 48 + revisão humana +24


def test_estimate_aplica_override_da_org():
    # override Escavador R$65,00 (6500) — igual ao validado na varredura do produto real.
    # escavador/targetdata/ia_deepseek são required em dados_partes -> entram na base.
    overrides = {"escavador": {"price_cents": 6500}}
    est = estimate("dados_partes", ["escavador", "targetdata", "ia_deepseek"], overrides)
    assert est["base_price_cents"] == 6500 + 3900 + 2900  # 13300
    assert est["total_price_cents"] == 13300


def test_effective_price_default_e_override():
    assert effective_module_price_cents("escavador") == 6000
    assert effective_module_price_cents("escavador", {"escavador": {"price_cents": 6500}}) == 6500


def test_cvs008_normalize_forca_obrigatorios_reuniao_equipe():
    # CVS-008: em reuniao_equipe (base 0) o cliente NAO pode remover os obrigatorios
    required = [c for c, cfg in pc.MATRIX.get("reuniao_equipe", {}).items() if cfg.required]
    assert required, "reuniao_equipe deve ter modulos obrigatorios"
    norm = normalize_selected_modules("reuniao_equipe", [])  # cliente nao enviou nada
    assert set(required) <= set(norm)
    # sem a normalizacao o preco seria 0 (furo); com ela, cobra os obrigatorios
    vuln = estimate("reuniao_equipe", [])["total_price_cents"]
    fixed = estimate("reuniao_equipe", norm)["total_price_cents"]
    assert vuln == 0 and fixed > 0


def test_cvs008_normalize_rejeita_modulo_desconhecido():
    with pytest.raises(ValueError):
        normalize_selected_modules("reuniao_equipe", ["modulo_inexistente_xyz"])
