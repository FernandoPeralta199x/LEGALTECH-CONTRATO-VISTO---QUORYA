"""Schemas Pydantic do módulo de pricing (estimate + admin config).

Portados de ``legaltech-aws/apps/api/src/modules/pricing/schemas.py``. A
validação de códigos (product/module) contra o catálogo é feita no handler
(retorna 400), já que aqui os códigos são strings livres.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.services.pricing.installments import InstallmentConfig  # reusa o model do domínio


class PricingEstimateRequest(BaseModel):
    """Corpo do ``POST /pricing/estimate``."""

    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=64)
    modules: list[str] = Field(default_factory=list)


class ProductOverrideInput(BaseModel):
    """Override parcial de um produto (admin)."""

    model_config = ConfigDict(extra="forbid")

    base_price_cents: int = Field(ge=0)


class ModuleOverrideInput(BaseModel):
    """Override parcial de um módulo (admin)."""

    model_config = ConfigDict(extra="forbid")

    price_cents: int = Field(ge=0)


class UpdatePricingConfigRequest(BaseModel):
    """Corpo do ``PUT /pricing/config`` (atualização parcial).

    Campos omitidos ficam inalterados (detecção via ``model_fields_set``).
    ``cases_limit = null`` significa explicitamente "sem limite".
    """

    model_config = ConfigDict(extra="forbid")

    cases_limit: int | None = Field(default=None, ge=1)
    product_overrides: dict[str, ProductOverrideInput] | None = None
    module_overrides: dict[str, ModuleOverrideInput] | None = None
    installment_config: InstallmentConfig | None = None
    notes: str | None = Field(default=None, max_length=500)


class PaymentSelectionSchema(BaseModel):
    """Corpo do POST /cases/{caseId}/payment. Backend deriva amount/org/case."""

    model_config = ConfigDict(extra="forbid")

    parcelas: int = Field(ge=1, le=24)
    method: Literal["pix", "boleto", "cartao"]
    pricing_config_version: int | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)
