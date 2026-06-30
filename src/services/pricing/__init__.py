"""Catálogo de pricing (produtos × módulos) + estimativa — fonte da verdade serverless,
portada de ``legaltech-aws/apps/api/src/modules/pricing``.
"""
from src.services.pricing.config import (
    MATRIX,
    MODULES,
    PRICING_CURRENCY,
    PRICING_VERSION,
    PRODUCTS,
    compute_product_base_price,
)
from src.services.pricing.estimate import effective_module_price_cents, estimate

__all__ = [
    "PRODUCTS", "MODULES", "MATRIX", "compute_product_base_price",
    "PRICING_CURRENCY", "PRICING_VERSION", "estimate", "effective_module_price_cents",
]
