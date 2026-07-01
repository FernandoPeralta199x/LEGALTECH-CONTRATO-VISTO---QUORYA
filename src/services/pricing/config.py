"""Catálogo canônico de pricing (serverless) — fonte da verdade de preços de módulos e
da matriz produto × módulo. Portado IDÊNTICO de
``legaltech-aws/apps/api/src/modules/pricing/config.py``.

Produtos não têm preço fixo: o preço é derivado dos módulos marcados como ``required``
para cada produto. A tabela editável de preços no admin (pricing_configs) sobrepõe estes
padrões (ver ``src/services/pricing/estimate.py``).
"""
from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field


PRICING_CURRENCY: Final[str] = "BRL"
PRICING_VERSION: Final[str] = "2026-06-21"

# Ajustes de SLA (em horas).
SLA_HUMAN_REVIEW_EXTRA_HOURS: Final[int] = 24
SLA_MEETING_PRODUCT_EXTRA_HOURS: Final[int] = 24

HUMAN_REVIEW_MODULE: Final[str] = "revisao_humana"
MEETING_PRODUCT: Final[str] = "reuniao_equipe"


ProductCode = Literal[
    "dados_partes",
    "consulta_objeto",
    "analise_contratual",
    "reuniao_equipe",
]

ModuleCode = Literal[
    "escavador",
    "targetdata",
    "ia_deepseek",
    "serasa_procon",
    "analise_contratual_ia",
    "revisao_humana",
    "reuniao_equipe",
]


class ProductMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    title: str
    description: str
    includes: tuple[str, ...] = ()
    base_price_cents: int = Field(ge=0)
    sla_hours: int = Field(ge=0)


class ModuleMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    title: str
    description: str
    price_cents: int = Field(ge=0)


class ModuleMatrixConfig(BaseModel):
    """Comportamento padrão de um módulo num produto (dicas do wizard)."""

    model_config = ConfigDict(extra="forbid")

    default: bool = False
    recommended: bool = False
    required: bool = False
    locked: bool = False


MODULES: Final[dict[str, ModuleMeta]] = {
    "escavador": ModuleMeta(
        code="escavador",
        title="Escavador",
        description="Conector planejado para processos judiciais, histórico jurídico e dados públicos.",
        price_cents=6000,
    ),
    "targetdata": ModuleMeta(
        code="targetdata",
        title="TargetData",
        description="Conector planejado para dados cadastrais, comerciais e enriquecimento.",
        price_cents=3900,
    ),
    "ia_deepseek": ModuleMeta(
        code="ia_deepseek",
        title="IA planejada",
        description="Módulo planejado para organizar dados, resumir informações e apoiar riscos.",
        price_cents=2900,
    ),
    "serasa_procon": ModuleMeta(
        code="serasa_procon",
        title="Serasa / Procon",
        description="Conector planejado para indicadores futuros de score, restrições, reputação e reclamações.",
        price_cents=5900,
    ),
    "analise_contratual_ia": ModuleMeta(
        code="analise_contratual_ia",
        title="Análise contratual assistida planejada",
        description="Módulo planejado para apoiar leitura, riscos e obrigações contratuais.",
        price_cents=7900,
    ),
    "revisao_humana": ModuleMeta(
        code="revisao_humana",
        title="Revisão humana planejada",
        description="Etapa preparada para futura avaliação da equipe ou advogado responsável.",
        price_cents=12900,
    ),
    "reuniao_equipe": ModuleMeta(
        code="reuniao_equipe",
        title="Reunião com advogado",
        description="Preparação local para uma futura etapa com profissional jurídico.",
        price_cents=49000,
    ),
}


MATRIX: Final[dict[str, dict[str, ModuleMatrixConfig]]] = {
    "dados_partes": {
        "escavador": ModuleMatrixConfig(default=True, required=True, locked=True),
        "targetdata": ModuleMatrixConfig(default=True, required=True, locked=True),
        "ia_deepseek": ModuleMatrixConfig(default=True, required=True, locked=True),
        "serasa_procon": ModuleMatrixConfig(default=False, recommended=True),
        "analise_contratual_ia": ModuleMatrixConfig(default=False),
        "revisao_humana": ModuleMatrixConfig(default=False),
        "reuniao_equipe": ModuleMatrixConfig(default=False),
    },
    "consulta_objeto": {
        "escavador": ModuleMatrixConfig(default=False),
        "targetdata": ModuleMatrixConfig(default=False),
        "ia_deepseek": ModuleMatrixConfig(default=True, required=True, locked=True),
        "serasa_procon": ModuleMatrixConfig(default=False),
        "analise_contratual_ia": ModuleMatrixConfig(default=False),
        "revisao_humana": ModuleMatrixConfig(default=False),
        "reuniao_equipe": ModuleMatrixConfig(default=False),
    },
    "analise_contratual": {
        "escavador": ModuleMatrixConfig(default=False),
        "targetdata": ModuleMatrixConfig(default=False),
        "ia_deepseek": ModuleMatrixConfig(default=True, required=True, locked=True),
        "serasa_procon": ModuleMatrixConfig(default=False),
        "analise_contratual_ia": ModuleMatrixConfig(default=True, required=True, locked=True),
        "revisao_humana": ModuleMatrixConfig(default=True, recommended=True),
        "reuniao_equipe": ModuleMatrixConfig(default=False),
    },
    "reuniao_equipe": {
        "escavador": ModuleMatrixConfig(default=False),
        "targetdata": ModuleMatrixConfig(default=False),
        "ia_deepseek": ModuleMatrixConfig(default=True, recommended=True),
        "serasa_procon": ModuleMatrixConfig(default=False),
        "analise_contratual_ia": ModuleMatrixConfig(default=False, recommended=True),
        "revisao_humana": ModuleMatrixConfig(default=False, required=True),
        "reuniao_equipe": ModuleMatrixConfig(default=False, required=True),
    },
}


def compute_product_base_price(
    product_code: str,
    modules: dict[str, ModuleMeta] | None = None,
    matrix: dict[str, dict[str, ModuleMatrixConfig]] | None = None,
) -> int:
    """Preço base do produto = soma dos módulos ``required``.

    Exceção: ``reuniao_equipe`` começa em 0 (seus módulos "fixos no roteiro" são opt-in
    e só entram no total quando habilitados — ver ``estimate``)."""
    if product_code == "reuniao_equipe":
        return 0
    mods = MODULES if modules is None else modules
    mx = MATRIX if matrix is None else matrix
    product_matrix = mx.get(product_code, {})
    return sum(
        mods[module_code].price_cents
        for module_code, config in product_matrix.items()
        if config.required and module_code in mods
    )


PRODUCTS: Final[dict[str, ProductMeta]] = {
    "dados_partes": ProductMeta(
        code="dados_partes",
        title="Dados das partes",
        description="Simulação local dos dados das partes para preparar futuras consultas.",
        includes=("Critério cadastral simulado", "Histórico jurídico futuro", "Reputação pública futura"),
        base_price_cents=compute_product_base_price("dados_partes"),
        sla_hours=24,
    ),
    "consulta_objeto": ProductMeta(
        code="consulta_objeto",
        title="Consulta do objeto",
        description="Composição local do objeto contratual e critérios de análise futura.",
        includes=("Critério simulado do objeto", "Pesquisa pública futura", "Resumo por IA planejada"),
        base_price_cents=compute_product_base_price("consulta_objeto"),
        sla_hours=24,
    ),
    "analise_contratual": ProductMeta(
        code="analise_contratual",
        title="Análise contratual",
        description="Simulação local de critérios para leitura contratual e riscos.",
        includes=("IA planejada", "Critérios de risco simulados", "Mapeamento simulado de obrigações"),
        base_price_cents=compute_product_base_price("analise_contratual"),
        sla_hours=48,
    ),
    "reuniao_equipe": ProductMeta(
        code="reuniao_equipe",
        title="Reunião com advogado",
        description="Preparação local para uma futura etapa com profissional jurídico.",
        includes=("Critérios prévios", "Reunião planejada", "Roteiro para parecer futuro"),
        base_price_cents=compute_product_base_price("reuniao_equipe"),
        sla_hours=72,
    ),
}
