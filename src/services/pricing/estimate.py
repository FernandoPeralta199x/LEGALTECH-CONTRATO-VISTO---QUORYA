"""Estimativa de pedido (centavos + SLA + breakdown), aplicando overrides por
organização (``pricing_configs.module_overrides``). Espelha
``PricingService.estimate`` do legaltech-aws: o preço do produto é o ``base``
(soma dos módulos ``required``) + ``modules_total`` (módulos selecionados que
NÃO são required). Em ``reuniao_equipe`` a base é 0 e os módulos "fixos no
roteiro" são opt-in, então os required também entram no total.
"""
from __future__ import annotations

from src.services.pricing.config import (
    HUMAN_REVIEW_MODULE,
    MATRIX,
    MEETING_PRODUCT,
    MODULES,
    PRICING_CURRENCY,
    PRODUCTS,
    SLA_HUMAN_REVIEW_EXTRA_HOURS,
    SLA_MEETING_PRODUCT_EXTRA_HOURS,
    ModuleMatrixConfig,
    compute_product_base_price,
)


def effective_module_price_cents(module_code: str, module_overrides: dict | None = None) -> int:
    """Preço do módulo aplicando o override da organização (se houver); senão, o padrão."""
    base = MODULES[module_code].price_cents
    if module_overrides:
        ov = module_overrides.get(module_code)
        if isinstance(ov, dict) and ov.get("price_cents") is not None:
            return int(ov["price_cents"])
    return base


def priced_modules(module_overrides: dict | None = None) -> dict:
    """``MODULES`` com os preços efetivos (override aplicado por código)."""
    return {
        code: meta.model_copy(
            update={"price_cents": effective_module_price_cents(code, module_overrides)})
        for code, meta in MODULES.items()
    }


def _unique_preserving_order(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def estimate(product_code: str, selected_module_codes, module_overrides: dict | None = None) -> dict:
    """Estimativa completa (espelha o legaltech-aws).

    Retorna ``{product, currency, base_price_cents, modules[], modules_total_cents,
    total_price_cents, sla_hours}``. ``base_price_cents`` deriva dos módulos
    ``required`` do produto (com overrides); ``modules_total_cents`` soma os
    módulos selecionados não-required (em ``reuniao_equipe``, soma todos).
    """
    priced = priced_modules(module_overrides)
    base_price = compute_product_base_price(product_code, modules=priced, matrix=MATRIX)
    product_matrix = MATRIX.get(product_code, {})
    include_required = product_code == MEETING_PRODUCT
    unique = _unique_preserving_order(list(selected_module_codes))

    line_items: list[dict] = []
    modules_total = 0
    for code in unique:
        if code not in MODULES:
            continue
        price = priced[code].price_cents
        line_items.append({"code": MODULES[code].code, "title": MODULES[code].title,
                           "price_cents": price})
        is_required = product_matrix.get(code, ModuleMatrixConfig()).required
        if include_required or not is_required:
            modules_total += price

    sla = PRODUCTS[product_code].sla_hours if product_code in PRODUCTS else 0
    if HUMAN_REVIEW_MODULE in unique:
        sla += SLA_HUMAN_REVIEW_EXTRA_HOURS
    if product_code == MEETING_PRODUCT:
        sla += SLA_MEETING_PRODUCT_EXTRA_HOURS

    return {
        "product": product_code,
        "currency": PRICING_CURRENCY,
        "base_price_cents": base_price,
        "modules": line_items,
        "modules_total_cents": modules_total,
        "total_price_cents": base_price + modules_total,
        "sla_hours": sla,
    }
