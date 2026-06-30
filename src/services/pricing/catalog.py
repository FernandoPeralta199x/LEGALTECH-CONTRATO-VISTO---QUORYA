"""Catálogo de pricing (produtos × módulos + matriz + regras de SLA) com os
overrides de módulo da organização aplicados. Espelha
``PricingService.get_catalog`` do legaltech-aws: o preço de cada produto é
derivado dos módulos ``required`` (já com override), não de um preço fixo.
"""
from __future__ import annotations

from src.services.pricing.config import (
    HUMAN_REVIEW_MODULE,
    MATRIX,
    MEETING_PRODUCT,
    MODULES,
    PRICING_CURRENCY,
    PRICING_VERSION,
    PRODUCTS,
    SLA_HUMAN_REVIEW_EXTRA_HOURS,
    SLA_MEETING_PRODUCT_EXTRA_HOURS,
    compute_product_base_price,
)
from src.services.pricing.estimate import effective_module_price_cents


def build_catalog(module_overrides: dict | None = None) -> dict:
    """Catálogo serializável (JSON) com overrides de módulo aplicados.

    ``module_overrides`` é o ``pricing_configs.module_overrides`` da org
    (``{code: {price_cents: N}}``). Os preços de produto são recomputados a
    partir dos módulos com override.
    """
    modules = [
        m.model_copy(update={"price_cents": effective_module_price_cents(code, module_overrides)})
        for code, m in MODULES.items()
    ]
    module_map = {m.code: m for m in modules}
    products = [
        p.model_copy(update={"base_price_cents": compute_product_base_price(
            code, modules=module_map, matrix=MATRIX)})
        for code, p in PRODUCTS.items()
    ]
    return {
        "currency": PRICING_CURRENCY,
        "version": PRICING_VERSION,
        "products": [p.model_dump(mode="json") for p in products],
        "modules": [m.model_dump(mode="json") for m in modules],
        "matrix": {pc: {mc: cfg.model_dump() for mc, cfg in mods.items()}
                   for pc, mods in MATRIX.items()},
        "sla_rules": {
            "human_review_extra_hours": SLA_HUMAN_REVIEW_EXTRA_HOURS,
            "meeting_product_extra_hours": SLA_MEETING_PRODUCT_EXTRA_HOURS,
            "human_review_module": HUMAN_REVIEW_MODULE,
            "meeting_product": MEETING_PRODUCT,
        },
    }
