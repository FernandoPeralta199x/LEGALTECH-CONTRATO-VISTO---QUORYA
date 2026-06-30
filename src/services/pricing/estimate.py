"""Estimativa de pedido: total (centavos) + SLA + breakdown dos módulos selecionados,
aplicando overrides por organização (``pricing_configs.module_overrides``). Espelha a
lógica de ``PricingService.estimate`` do legaltech-aws.
"""
from __future__ import annotations

from src.services.pricing.config import (
    HUMAN_REVIEW_MODULE,
    MODULES,
    PRODUCTS,
    SLA_HUMAN_REVIEW_EXTRA_HOURS,
)


def effective_module_price_cents(module_code: str, module_overrides: dict | None = None) -> int:
    """Preço do módulo aplicando o override da organização (se houver); senão, o padrão."""
    base = MODULES[module_code].price_cents
    if module_overrides:
        ov = module_overrides.get(module_code)
        if isinstance(ov, dict) and ov.get("price_cents") is not None:
            return int(ov["price_cents"])
    return base


def estimate(product_code: str, selected_module_codes, module_overrides: dict | None = None) -> dict:
    """Total (centavos) + SLA (horas) + breakdown dos módulos selecionados válidos.

    Aplica overrides de preço por organização. Revisão humana adiciona SLA extra.
    """
    breakdown: list[dict] = []
    total = 0
    selected = list(selected_module_codes)
    for code in selected:
        if code not in MODULES:
            continue
        price = effective_module_price_cents(code, module_overrides)
        total += price
        breakdown.append({"code": code, "title": MODULES[code].title, "price_cents": price})
    sla = PRODUCTS[product_code].sla_hours if product_code in PRODUCTS else 0
    if HUMAN_REVIEW_MODULE in set(selected):
        sla += SLA_HUMAN_REVIEW_EXTRA_HOURS
    return {
        "product_code": product_code,
        "total_cents": total,
        "currency": "BRL",
        "sla_hours": sla,
        "breakdown": breakdown,
    }
