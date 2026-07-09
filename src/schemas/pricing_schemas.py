"""Schemas Pydantic do módulo de pricing (estimate + admin config).

Portados de ``legaltech-aws/apps/api/src/modules/pricing/schemas.py``. A
validação de códigos (product/module) contra o catálogo é feita no handler
(retorna 400), já que aqui os códigos são strings livres.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.services.pricing.installments import InstallmentConfig  # reusa o model do domínio


class PricingEstimateRequest(BaseModel):
    """Corpo do ``POST /pricing/estimate``."""

    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=64)
    modules: list[str] = Field(default_factory=list)


# Teto de preço (R$ 1.000.000,00) — evita overflow/valores absurdos em overrides
# que quebrariam cobrança/UI/cálculos (achado do pentest 2026-07-09).
MAX_PRICE_CENTS = 100_000_000


class ProductOverrideInput(BaseModel):
    """Override parcial de um produto (admin)."""

    model_config = ConfigDict(extra="forbid")

    base_price_cents: int = Field(ge=0, le=MAX_PRICE_CENTS)


class ModuleOverrideInput(BaseModel):
    """Override parcial de um módulo (admin)."""

    model_config = ConfigDict(extra="forbid")

    price_cents: int = Field(ge=0, le=MAX_PRICE_CENTS)


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
    method: Literal["pix", "boleto", "cartao", "debito"]
    pricing_config_version: int | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)
    # Cartão: só o token + hints de exibição. NUNCA número/CVV/validade (extra=forbid rejeita).
    card_token: str | None = Field(default=None, max_length=255)
    card_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    card_brand: str | None = Field(default=None, max_length=20)
    card_holder: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _cartao_exige_token(self) -> "PaymentSelectionSchema":
        # Débito é um cartão: mesma tokenização client-side que crédito ("cartao").
        if self.method in ("cartao", "debito") and not self.card_token:
            raise ValueError("cartão exige card_token (tokenização client-side)")
        return self

    @model_validator(mode="after")
    def _debito_e_a_vista(self) -> "PaymentSelectionSchema":
        # Débito é sempre à vista: parcelas == 1 (vira 400 no handler se != 1).
        if self.method == "debito" and self.parcelas != 1:
            raise ValueError("débito é à vista: parcelas deve ser 1")
        return self
