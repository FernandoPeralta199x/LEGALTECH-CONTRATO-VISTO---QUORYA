# src/services/pricing/installments.py
"""Cálculo puro de opções de parcelamento (sem I/O). Dinheiro em centavos; juros em bps.
Sem juros: total // N com resíduo na última. Com juros: tabela Price em Decimal (Task 2)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel, ConfigDict, Field, model_validator

_METHODS = ("pix", "boleto", "cartao")
_CURRENCY = "BRL"


class MethodRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_parcelas: int = Field(default=1, ge=1, le=24)


class InstallmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    max_parcelas: int = Field(default=1, ge=1, le=24)
    sem_juros_ate: int = Field(default=1, ge=1, le=24)
    juros_mensal_bps: int = Field(default=0, ge=0)
    valor_minimo_parcela_cents: int = Field(default=0, ge=0)
    primeiro_vencimento_dias: int = Field(default=30, ge=0, le=365)
    dia_vencimento: int | None = Field(default=None, ge=1, le=28)
    allowed_methods: dict[str, MethodRule] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _cross(self) -> "InstallmentConfig":
        if self.sem_juros_ate > self.max_parcelas:
            raise ValueError("sem_juros_ate não pode exceder max_parcelas")
        for m, rule in self.allowed_methods.items():
            if m not in _METHODS:
                raise ValueError(f"método inválido: {m}")
            if rule.max_parcelas > self.max_parcelas:
                raise ValueError(f"{m}.max_parcelas excede max_parcelas")
        return self


def add_months(base: date, months: int) -> date:
    m = base.month - 1 + months
    year = base.year + m // 12
    month = m % 12 + 1
    return date(year, month, min(base.day, 28))


def _effective_methods(config: InstallmentConfig) -> dict[str, MethodRule]:
    if config.allowed_methods:
        return config.allowed_methods
    return {"pix": MethodRule(enabled=True, max_parcelas=1),
            "boleto": MethodRule(enabled=True, max_parcelas=1),
            "cartao": MethodRule(enabled=True, max_parcelas=config.max_parcelas)}


def _methods_for(n: int, config: InstallmentConfig) -> list[str]:
    methods = _effective_methods(config)
    return [m for m in _METHODS
            if (r := methods.get(m)) and r.enabled and r.max_parcelas >= n]


def _due_dates(n: int, reference_date: date, config: InstallmentConfig) -> list[date]:
    base = reference_date + timedelta(days=config.primeiro_vencimento_dias)
    if config.dia_vencimento is not None:
        day = min(config.dia_vencimento, 28)
        first = base.replace(day=day)
        if first < base:
            first = add_months(base, 1).replace(day=day)
    else:
        first = base
    return [add_months(first, k) for k in range(n)]


def _amounts(total_cents: int, n: int, config: InstallmentConfig) -> tuple[list[int], int, int, bool]:
    if total_cents < 0:
        raise ValueError("total_cents não pode ser negativo")
    if n <= config.sem_juros_ate or config.juros_mensal_bps == 0:
        base = total_cents // n
        amounts = [base] * n
        amounts[-1] += total_cents - base * n
        return amounts, total_cents, 0, False
    return _amounts_price(total_cents, n, config)  # Task 2


def _amounts_price(total_cents: int, n: int, config: InstallmentConfig):
    raise NotImplementedError  # implementado na Task 2


def compute_installment_options(total_cents: int, config: InstallmentConfig,
                                reference_date: date) -> list[dict]:
    if total_cents < 0:
        raise ValueError("total_cents não pode ser negativo")
    # total 0 ou config desabilitada => apenas 1x (spec §4.6)
    max_n = config.max_parcelas if (config.enabled and total_cents > 0) else 1
    options: list[dict] = []
    for n in range(1, max_n + 1):
        try:
            amounts, valor_total, acrescimo, has_juros = _amounts(total_cents, n, config)
        except NotImplementedError:  # opções com juros chegam na Task 2
            continue
        parcela = amounts[0] if amounts else 0
        if n > 1 and parcela < config.valor_minimo_parcela_cents:
            continue
        dates = _due_dates(n, reference_date, config)
        schedule = [{"numero": k + 1, "vencimento": dates[k].isoformat(),
                     "valor_cents": amounts[k]} for k in range(n)]
        options.append({
            "parcelas": n,
            "has_juros": has_juros,
            "juros_mensal_bps": config.juros_mensal_bps if has_juros else 0,
            "valor_parcela_cents": parcela,
            "valor_total_cents": valor_total,
            "acrescimo_cents": acrescimo,
            "currency": _CURRENCY,
            "schedule": schedule,
            "allowed_methods": _methods_for(n, config),
        })
    return options
