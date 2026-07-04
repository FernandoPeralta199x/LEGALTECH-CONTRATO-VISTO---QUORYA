# Sistema de Parcelas + Seam de Pagamento — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parcelamento configurável por organização + pagamento simulado pós-registro (tela dedicada), com seam pronto para gateway real.

**Architecture:** Domínio puro (`installments.py`, `Decimal`/Price) calcula opções; `payment.py` isola o provider (Mock agora, Real por env); `POST /requests` cria o caso `pending`; novo `POST /cases/{caseId}/payment` aplica o plano; frontend ganha card de config no admin e tela `/cases/[id]/pagamento`.

**Tech Stack:** Python 3.11 serverless (Lambda handlers `(event, context)`), PostgreSQL 18 + RLS (PG18 docker `cv-pg18` porta 5433), pytest; Next.js 16 + TypeScript (eslint/tsc/`tsx --test`/build).

**Spec:** `docs/superpowers/specs/2026-07-03-pricing-parcelas-design.md` (V4).

**Convenções do repo (não reinventar):**
- Testes de handler: `_event(user_id, role="admin", body=None, path=None, org_id=SYSTEM_ORG)`, `_admin_conn()` (dbadmin/localdev_cv@5433), `_data(resp)` (assert 200/201). Ver `tests/test_pricing_handlers.py`.
- Rotas: adicionar bloco em `serverless.yml`; `tools/local_server.py` auto-registra (parse de `functions[].events[].http`).
- Migração aplicada como `dbadmin` via `docker exec -i cv-pg18 psql -U dbadmin -d contrato_visto`.
- Rodar testes: `cd backend && ./.venv/Scripts/python.exe -m pytest <arquivo> -v`.
- Erros de validação = **400** (nunca 422). Dinheiro em centavos inteiros; juros em bps.

---

## Mapa de arquivos

**Backend — criar:** `src/services/pricing/installments.py`, `src/adapters/payment.py`, `src/handlers/payments.py`, `migrations/017_installments.sql`, `tests/test_installments.py`, `tests/test_payment_adapter.py`, `tests/test_payments_handler.py`.
**Backend — modificar:** `src/schemas/pricing_schemas.py`, `src/handlers/pricing.py`, `src/handlers/requests.py`, `src/handlers/cases.py`, `serverless.yml`, `tests/test_pricing_handlers.py`.
**Frontend — criar:** `components/pricing/InstallmentConfigCard.tsx`, `src/app/cases/[id]/pagamento/page.tsx`.
**Frontend — modificar:** `src/services/pricing.ts`, `src/services/cases.ts`, `src/app/admin/pricing/page.tsx`, `src/app/cases/[id]/page.tsx`.

Ordem: domínio → adapter → migração → schemas → pricing → requests → payments+rota → cases(read) → frontend → gate final.

---

## Task 1: Domínio — config + parcelamento sem juros

**Files:**
- Create: `src/services/pricing/installments.py`
- Test: `tests/test_installments.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_installments.py
from datetime import date
from src.services.pricing.installments import InstallmentConfig, compute_installment_options

REF = date(2026, 7, 3)

def _cfg(**kw):
    base = dict(enabled=True, max_parcelas=12, sem_juros_ate=3, juros_mensal_bps=299,
                valor_minimo_parcela_cents=0, primeiro_vencimento_dias=30, dia_vencimento=10,
                allowed_methods={"pix": {"enabled": True, "max_parcelas": 1},
                                 "boleto": {"enabled": True, "max_parcelas": 1},
                                 "cartao": {"enabled": True, "max_parcelas": 12}})
    base.update(kw)
    return InstallmentConfig(**base)

def test_sem_juros_divide_exato_e_ultima_absorve_residuo():
    opts = compute_installment_options(10000, _cfg(), REF)
    by_n = {o["parcelas"]: o for o in opts}
    assert by_n[1]["valor_total_cents"] == 10000 and by_n[1]["has_juros"] is False
    tres = by_n[3]
    assert tres["has_juros"] is False and tres["valor_total_cents"] == 10000
    assert sum(i["valor_cents"] for i in tres["schedule"]) == 10000
    assert tres["schedule"][-1]["valor_cents"] == 3334  # 3333,3333,3334

def test_config_desabilitada_so_1x():
    opts = compute_installment_options(10000, _cfg(enabled=False), REF)
    assert [o["parcelas"] for o in opts] == [1]

def test_total_zero_retorna_1x_de_zero():
    opts = compute_installment_options(0, _cfg(), REF)
    assert opts[0]["parcelas"] == 1 and opts[0]["valor_total_cents"] == 0

def test_total_negativo_falha():
    import pytest
    with pytest.raises(ValueError):
        compute_installment_options(-1, _cfg(), REF)

def test_sem_juros_ate_maior_que_max_falha():
    import pytest
    with pytest.raises(Exception):
        _cfg(sem_juros_ate=20, max_parcelas=12)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_installments.py -v`
Expected: FAIL (`ModuleNotFoundError: installments`).

- [ ] **Step 3: Implementar config + laço sem juros + montagem**

```python
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
    max_n = config.max_parcelas if config.enabled else 1
    options: list[dict] = []
    for n in range(1, max_n + 1):
        amounts, valor_total, acrescimo, has_juros = _amounts(total_cents, n, config)
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
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_installments.py -v`
Expected: PASS (os 5 testes; o de juros ainda não existe).

- [ ] **Step 5: Commit**

```bash
git add src/services/pricing/installments.py tests/test_installments.py
git commit -m "feat(pricing): dominio de parcelamento (config + sem juros)"
```

---

## Task 2: Domínio — juros (tabela Price) + invariante de soma exata

**Files:**
- Modify: `src/services/pricing/installments.py` (`_amounts_price`)
- Test: `tests/test_installments.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_com_juros_soma_exata_e_ultima_fecha():
    opts = compute_installment_options(23700, _cfg(), REF)
    seis = next(o for o in opts if o["parcelas"] == 6)
    assert seis["has_juros"] is True and seis["juros_mensal_bps"] == 299
    assert sum(i["valor_cents"] for i in seis["schedule"]) == seis["valor_total_cents"]
    assert seis["valor_total_cents"] > 23700 and seis["acrescimo_cents"] > 0
    assert all(i["valor_cents"] == seis["valor_parcela_cents"] for i in seis["schedule"][:-1])

def test_juros_zero_nunca_cobra_juros():
    opts = compute_installment_options(10000, _cfg(juros_mensal_bps=0), REF)
    assert all(o["has_juros"] is False for o in opts)

def test_valor_minimo_descarta_opcoes():
    opts = compute_installment_options(10000, _cfg(valor_minimo_parcela_cents=3000), REF)
    ns = [o["parcelas"] for o in opts]
    assert 1 in ns and max(ns) <= 3  # 4x=2500 < 3000 é descartado

def test_cronograma_vira_o_ano_e_dia_fixo():
    opts = compute_installment_options(23700, _cfg(primeiro_vencimento_dias=180), REF)
    seis = next(o for o in opts if o["parcelas"] == 6)
    dias = {i["vencimento"][8:10] for i in seis["schedule"]}
    assert dias == {"10"}  # todos no dia 10
    assert any(i["vencimento"].startswith("2027") for i in seis["schedule"])
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_installments.py::test_com_juros_soma_exata_e_ultima_fecha -v`
Expected: FAIL (`NotImplementedError`).

- [ ] **Step 3: Implementar Price com amortização**

Substituir `_amounts_price` por:

```python
def _amounts_price(total_cents: int, n: int, config: InstallmentConfig) -> tuple[list[int], int, int, bool]:
    i = Decimal(config.juros_mensal_bps) / Decimal(10000)
    pv = Decimal(total_cents)
    pmt = pv * i / (Decimal(1) - (Decimal(1) + i) ** -n)
    pmt_cents = int(pmt.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    amounts: list[int] = []
    saldo = total_cents
    for k in range(1, n + 1):
        juros_k = int((Decimal(saldo) * i).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if k < n:
            amounts.append(pmt_cents)
            saldo -= (pmt_cents - juros_k)
        else:
            amounts.append(saldo + juros_k)
            saldo = 0
    valor_total = sum(amounts)
    return amounts, valor_total, valor_total - total_cents, True
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_installments.py -v`
Expected: PASS (todos, incl. os da Task 1).

- [ ] **Step 5: Commit**

```bash
git add src/services/pricing/installments.py tests/test_installments.py
git commit -m "feat(pricing): parcelamento com juros (Price/Decimal, soma exata)"
```

---

## Task 3: Seam de pagamento — `payment.py` (Mock + factory + to_public)

**Files:**
- Create: `src/adapters/payment.py`
- Test: `tests/test_payment_adapter.py`

- [ ] **Step 1: Teste que falha**

```python
# tests/test_payment_adapter.py
import os
import pytest
from src.adapters.payment import (PaymentRequest, MockPaymentProvider, RealPaymentProvider,
                                   create_payment_provider)

def _req(method="cartao"):
    return PaymentRequest(amount_cents=10000, installments=3, method=method,
                          case_reference="case-1", organization_id="org-1",
                          idempotency_key="k1", schedule=[{"numero": 1, "valor_cents": 3334}])

@pytest.mark.parametrize("method", ["pix", "boleto", "cartao"])
def test_mock_retorna_simulated_por_metodo(method):
    res = MockPaymentProvider().create_charge(_req(method))
    assert res.status == "simulated" and res.method == method
    assert res.external_reference and res.external_reference.startswith("mock_")
    pub = res.to_public()
    assert "raw" not in pub and pub["status"] == "simulated"

def test_factory_default_mock():
    prov = create_payment_provider()
    assert isinstance(prov, MockPaymentProvider)

def test_real_placeholder_falha_claro():
    prov = RealPaymentProvider(api_key="x")
    with pytest.raises(NotImplementedError):
        prov.create_charge(_req())

def test_factory_real_exige_api_key(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROVIDER", "pagarme")
    monkeypatch.setenv("PAYMENT_MODE", "sandbox")
    monkeypatch.delenv("PAYMENT_API_KEY", raising=False)
    with pytest.raises(ValueError):
        create_payment_provider()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_payment_adapter.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implementar o adapter**

```python
# src/adapters/payment.py
"""Seam de pagamento (serverless). Mock simulado agora; Real placeholder para gateway (env).
Espelha o padrão de src/adapters/procon.py."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol, runtime_checkable

Method = Literal["pix", "boleto", "cartao"]
Mode = Literal["mock", "sandbox", "live"]
Status = Literal["simulated", "pending", "paid", "failed", "canceled", "expired", "refunded"]
_SENSITIVE = {"raw"}


@dataclass(frozen=True)
class PaymentRequest:
    amount_cents: int
    installments: int
    method: Method
    case_reference: str
    organization_id: str
    idempotency_key: str
    schedule: list[dict]
    currency: str = "BRL"
    mode: Mode = "mock"


@dataclass(frozen=True)
class PaymentResult:
    provider: str
    mode: Mode
    status: Status
    method: Method
    external_reference: str | None
    payment_form: dict = field(default_factory=dict)
    requested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw: dict = field(default_factory=dict)

    def to_public(self) -> dict:
        return {"provider": self.provider, "mode": self.mode, "status": self.status,
                "method": self.method, "external_reference": self.external_reference,
                "payment_form": self.payment_form, "requested_at": self.requested_at}


@runtime_checkable
class PaymentProvider(Protocol):
    def create_charge(self, req: PaymentRequest) -> PaymentResult: ...


def _mock_form(method: Method) -> dict:
    if method == "pix":
        return {"type": "pix", "qr_code": "MOCK-PIX-QR", "copia_cola": "000201MOCK"}
    if method == "boleto":
        return {"type": "boleto", "url": "https://mock/boleto", "linha_digitavel": "00000.00000"}
    return {"type": "cartao", "authorization_code": "MOCK-AUTH-123"}


class MockPaymentProvider:
    def create_charge(self, req: PaymentRequest) -> PaymentResult:
        return PaymentResult(
            provider="mock", mode="mock", status="simulated", method=req.method,
            external_reference=f"mock_{req.case_reference}_{uuid.uuid4().hex[:8]}",
            payment_form=_mock_form(req.method))


class RealPaymentProvider:
    """Placeholder — implementação real na AWS (sandbox/live). Requer PAYMENT_API_KEY."""
    def __init__(self, provider: str, mode: Mode, api_key: str) -> None:
        self._provider, self._mode, self._api_key = provider, mode, api_key

    def create_charge(self, req: PaymentRequest) -> PaymentResult:
        raise NotImplementedError(
            "RealPaymentProvider não implementado — aguardando gateway (impl. AWS).")


def create_payment_provider(provider: str | None = None, mode: str | None = None,
                            api_key: str | None = None) -> PaymentProvider:
    provider = provider or os.getenv("PAYMENT_PROVIDER", "mock")
    mode = mode or os.getenv("PAYMENT_MODE", "mock")
    api_key = api_key or os.getenv("PAYMENT_API_KEY")
    if provider == "mock" or mode == "mock":
        return MockPaymentProvider()
    if not api_key:
        raise ValueError("PAYMENT_API_KEY obrigatória para provider real")
    return RealPaymentProvider(provider=provider, mode=mode, api_key=api_key)  # type: ignore[arg-type]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_payment_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adapters/payment.py tests/test_payment_adapter.py
git commit -m "feat(payment): seam de pagamento (Mock+Real placeholder+factory)"
```

---

## Task 4: Migração 017 — colunas de parcelamento/pagamento

**Files:**
- Create: `migrations/017_installments.sql`

- [ ] **Step 1: Escrever a migração**

```sql
-- migrations/017_installments.sql
BEGIN;
ALTER TABLE public.pricing_configs
  ADD COLUMN IF NOT EXISTS installment_config jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.requests
  ADD COLUMN IF NOT EXISTS installment_plan jsonb,
  ADD COLUMN IF NOT EXISTS payment_status text NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS pricing_config_version integer;
COMMIT;
```

- [ ] **Step 2: Aplicar como dbadmin**

Run:
```bash
docker exec -i cv-pg18 psql -U dbadmin -d contrato_visto < migrations/017_installments.sql
```
Expected: `BEGIN`, `ALTER TABLE` (x2), `COMMIT`.

- [ ] **Step 3: Verificar colunas**

Run:
```bash
docker exec cv-pg18 psql -U dbadmin -d contrato_visto -c "\d public.requests" | grep -E "installment_plan|payment_status|pricing_config_version"
```
Expected: as 3 colunas presentes; `payment_status` com default `'pending'`.

- [ ] **Step 4: Commit**

```bash
git add migrations/017_installments.sql
git commit -m "feat(db): migration 017 (installment_config + payment em requests)"
```

---

## Task 5: Schemas — config de parcelamento + seleção de pagamento

**Files:**
- Modify: `src/schemas/pricing_schemas.py`

- [ ] **Step 1: Teste que falha** (em `tests/test_pricing_handlers.py`, adicionar)

```python
def test_schema_payment_selection_valida():
    from src.schemas.pricing_schemas import PaymentSelectionSchema
    s = PaymentSelectionSchema(parcelas=3, method="cartao", idempotency_key="k")
    assert s.parcelas == 3 and s.method == "cartao"
    import pytest
    with pytest.raises(Exception):
        PaymentSelectionSchema(parcelas=0, method="pix", idempotency_key="k")
    with pytest.raises(Exception):
        PaymentSelectionSchema(parcelas=1, method="doge", idempotency_key="k")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pricing_handlers.py::test_schema_payment_selection_valida -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implementar os schemas**

Adicionar em `src/schemas/pricing_schemas.py`:

```python
from typing import Literal, Optional
from src.services.pricing.installments import InstallmentConfig  # reusa o model do domínio


class UpdatePricingConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases_limit: int | None = Field(default=None, ge=1)
    product_overrides: dict[str, ProductOverrideInput] | None = None
    module_overrides: dict[str, ModuleOverrideInput] | None = None
    installment_config: InstallmentConfig | None = None   # NOVO
    notes: str | None = Field(default=None, max_length=500)


class PaymentSelectionSchema(BaseModel):
    """Corpo do POST /cases/{caseId}/payment. Backend deriva amount/org/case."""
    model_config = ConfigDict(extra="forbid")
    parcelas: int = Field(ge=1, le=24)
    method: Literal["pix", "boleto", "cartao"]
    pricing_config_version: int | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)
```

(Manter os campos já existentes de `UpdatePricingConfigRequest`; apenas acrescentar `installment_config`.)

- [ ] **Step 4: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pricing_handlers.py::test_schema_payment_selection_valida -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/schemas/pricing_schemas.py tests/test_pricing_handlers.py
git commit -m "feat(pricing): schemas de installment_config e PaymentSelection"
```

---

## Task 6: `pricing.py` — estimate com opções + config lê/grava installment_config

**Files:**
- Modify: `src/handlers/pricing.py`
- Test: `tests/test_pricing_handlers.py`

- [ ] **Step 1: Teste que falha**

```python
def test_estimate_inclui_installment_options_e_version():
    a = str(uuid.uuid4())
    # admin habilita parcelamento
    cfg = {"installment_config": {"enabled": True, "max_parcelas": 6, "sem_juros_ate": 3,
           "juros_mensal_bps": 299, "valor_minimo_parcela_cents": 0,
           "primeiro_vencimento_dias": 30, "dia_vencimento": 10,
           "allowed_methods": {"cartao": {"enabled": True, "max_parcelas": 6}}}}
    pr_h.update_pricing_config(_event(a, body=cfg), None)
    resp = pr_h.estimate_pricing(_event(a, body={"product": "analise_contratual", "modules": []}), None)
    data = _data(resp)
    assert "installment_options" in data and len(data["installment_options"]) >= 1
    assert "pricing_config_version" in data and "payment_mode" in data
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pricing_handlers.py::test_estimate_inclui_installment_options_e_version -v`
Expected: FAIL (`KeyError: installment_options`).

- [ ] **Step 3: Implementar**

Em `src/handlers/pricing.py`:

1. Novo helper de leitura de config completa (junto do `_org_module_overrides`):

```python
import os
from datetime import date, datetime, timezone, timedelta
from src.services.pricing.installments import InstallmentConfig, compute_installment_options

_BRT = timezone(timedelta(hours=-3))

def _org_installment(cur, org) -> tuple[InstallmentConfig, int]:
    cur.execute("SELECT installment_config, version FROM public.pricing_configs"
                " WHERE organization_id = %s", (org,))
    row = cur.fetchone()
    raw = (row["installment_config"] if row else None) or {}
    version = (row["version"] if row else 0)
    try:
        cfg = InstallmentConfig(**raw)
    except Exception:
        cfg = InstallmentConfig()  # fail-safe: só 1x
    return cfg, version
```

2. No `estimate_pricing`, após `est = compute_estimate(...)`, ler config e anexar opções:

```python
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            overrides = _org_module_overrides(cur, user["organization_id"])
            est = compute_estimate(data.product, selected, overrides)
            icfg, iver = _org_installment(cur, user["organization_id"])
        ref = datetime.now(_BRT).date()
        est["installment_options"] = compute_installment_options(
            est["total_price_cents"], icfg, ref)
        est["pricing_config_version"] = iver
        est["payment_mode"] = os.getenv("PAYMENT_MODE", "mock")
```

(Simplificar a linha `ref` para: `ref = datetime.now(_BRT).date()` com `from datetime import datetime`.)

3. No `_config_payload`, incluir `installment_config` (default quando ausente):

```python
    # dentro do dict de retorno (row existente e no default):
    "installment_config": (row["installment_config"] if row and row["installment_config"] else {}),
```

E no `SELECT` de `get_pricing_config` e no `RETURNING` de `update_pricing_config`, adicionar a coluna `installment_config`.

4. Em `update_pricing_config`, tratar `installment_config` no merge parcial (igual aos outros campos), com `Json(...)`:

```python
            cur_inst = cur_row["installment_config"] if cur_row else {}
            new_inst = (data.installment_config.model_dump()
                        if "installment_config" in changed and data.installment_config
                        else cur_inst)
            # incluir installment_config no INSERT ... ON CONFLICT (colunas + EXCLUDED) com Json(new_inst)
```

(Adicionar `installment_config` à lista de colunas do `INSERT`, ao `VALUES`, ao `ON CONFLICT DO UPDATE`, e ao `SELECT` inicial `cur_row`.)

- [ ] **Step 4: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pricing_handlers.py -v`
Expected: PASS (novo + os existentes que leem config).

- [ ] **Step 5: Commit**

```bash
git add src/handlers/pricing.py tests/test_pricing_handlers.py
git commit -m "feat(pricing): estimate com installment_options + config installment_config"
```

---

## Task 7: `requests.py` — caso nasce pending (sem mudar o contrato de entrada)

**Files:**
- Modify: `src/handlers/requests.py:117-126` (INSERT em `public.requests`)
- Test: `tests/test_requests_handlers.py`

- [ ] **Step 1: Teste que falha**

```python
def test_request_nasce_pending_sem_plano():
    # (usar o helper de criação de request já existente no arquivo de teste)
    resp = _create_request(...)  # conforme o padrão do arquivo
    data = _data(resp)
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET app.organization_id = %s", (SYSTEM_ORG,))
        cur.execute("SELECT payment_status, installment_plan, pricing_config_version"
                    " FROM public.requests WHERE id = %s", (data["request_id"],))
        row = cur.fetchone()
    conn.close()
    assert row[0] == "pending" and row[1] is None and row[2] is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_requests_handlers.py::test_request_nasce_pending_sem_plano -v`
Expected: FAIL (colunas ausentes no INSERT — payment_status usa default, mas o teste garante o contrato).

- [ ] **Step 3: Implementar**

No INSERT de `public.requests` (linhas ~117-126), adicionar as colunas explicitamente:

```python
            cur.execute(
                "INSERT INTO public.requests"
                " (id, organization_id, created_by, code, product_type, product_label, title,"
                "  description, status, source_mode, idempotency_key, case_id,"
                "  total_price_cents, price_snapshot, payment_status, installment_plan,"
                "  pricing_config_version)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'created',%s,%s,NULL,%s,%s,'pending',NULL,NULL)",
                (request_id, org, uid, code, data.product_type, product_label, title,
                 data.description, data.source_mode, data.idempotency_key,
                 est["total_price_cents"], Json(est)),
            )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_requests_handlers.py -v`
Expected: PASS (novo + idempotência 409 existente intactos).

- [ ] **Step 5: Commit**

```bash
git add src/handlers/requests.py tests/test_requests_handlers.py
git commit -m "feat(requests): caso nasce com payment_status=pending"
```

---

## Task 8: `payments.py` — `POST /cases/{caseId}/payment` + rota

**Files:**
- Create: `src/handlers/payments.py`
- Modify: `serverless.yml` (novo bloco de função)
- Test: `tests/test_payments_handler.py`

- [ ] **Step 1: Teste que falha**

```python
# tests/test_payments_handler.py
import json, uuid, psycopg2, pytest
from src.handlers import payments as pay_h
from src.handlers import pricing as pr_h
# reusar _event/_admin_conn/_data/_create_case do padrão dos outros testes

def test_pagamento_grava_plano_e_status(seed_case_and_config):
    case_id, admin = seed_case_and_config  # caso pending + config habilitada
    body = {"parcelas": 3, "method": "cartao", "idempotency_key": "p1"}
    resp = pay_h.create_case_payment(_event(admin, body=body, path={"caseId": case_id}), None)
    data = _data(resp)
    assert data["payment_status"] == "simulated"
    assert data["installment_plan"]["parcelas"] == 3
    assert "raw" not in data["installment_plan"]["payment"]

def test_pagamento_rejeita_parcela_nao_ofertada(seed_case_and_config):
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(
        _event(admin, body={"parcelas": 99, "method": "cartao", "idempotency_key": "p2"},
               path={"caseId": case_id}), None)
    assert resp["statusCode"] == 400

def test_pagamento_idempotente_replay(seed_case_and_config):
    case_id, admin = seed_case_and_config
    b = {"parcelas": 3, "method": "cartao", "idempotency_key": "p3"}
    r1 = _data(pay_h.create_case_payment(_event(admin, body=b, path={"caseId": case_id}), None))
    r2 = _data(pay_h.create_case_payment(_event(admin, body=b, path={"caseId": case_id}), None))
    assert r1["installment_plan"]["payment"]["external_reference"] == \
           r2["installment_plan"]["payment"]["external_reference"]

def test_pagamento_ja_pago_rejeita_payload_diferente(seed_case_and_config):
    case_id, admin = seed_case_and_config
    _data(pay_h.create_case_payment(_event(admin, body={"parcelas": 3, "method": "cartao",
          "idempotency_key": "p4"}, path={"caseId": case_id}), None))
    resp = pay_h.create_case_payment(_event(admin, body={"parcelas": 6, "method": "cartao",
          "idempotency_key": "p4"}, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 409
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_payments_handler.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implementar o handler**

```python
# src/handlers/payments.py
"""POST /cases/{caseId}/payment — aplica um plano de parcelamento ao caso (pagamento simulado).
Recalcula server-side; idempotência própria (chave + hash do payload) em installment_plan.payment."""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta

from psycopg2.extras import Json
from pydantic import ValidationError

from src.adapters.payment import PaymentRequest, create_payment_provider
from src.schemas.pricing_schemas import PaymentSelectionSchema
from src.services.database import tenant_tx
from src.services.pricing.installments import InstallmentConfig, compute_installment_options
from src.utils.context import require_user
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import fmt_validation_error as _fmt, parse_json_body as _parse_body
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()
_BRT = timezone(timedelta(hours=-3))


def _payload_hash(sel: PaymentSelectionSchema) -> str:
    raw = json.dumps({"parcelas": sel.parcelas, "method": sel.method}, sort_keys=True)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


@require_user
def create_case_payment(event, context):
    user = event["user"]
    org = user["organization_id"]
    case_id = (event.get("pathParameters") or {}).get("caseId")
    if not case_id:
        return error_response(400, "caseId ausente")
    body, err = _parse_body(event)
    if err:
        return err
    try:
        sel = PaymentSelectionSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute(
                "SELECT r.id, r.total_price_cents, r.installment_plan, r.payment_status"
                " FROM public.requests r JOIN public.cases c ON c.request_id = r.id"
                " WHERE c.id = %s", (case_id,))
            row = cur.fetchone()
            if not row:
                return error_response(404, "Caso não encontrado")

            plan = row["installment_plan"]
            new_hash = _payload_hash(sel)
            if plan and plan.get("payment"):
                same_key = plan["payment"].get("idempotency_key") == sel.idempotency_key
                if same_key and plan["payment"].get("payload_hash") == new_hash:
                    return success_response(200, "Pagamento já registrado",
                                            {"payment_status": row["payment_status"],
                                             "installment_plan": plan})
                return error_response(409, "Pagamento já registrado com outros dados")

            icfg, iver = _read_config(cur, org)
            total = row["total_price_cents"] or 0
            ref = datetime.now(_BRT).date()
            options = compute_installment_options(total, icfg, ref)
            opt = next((o for o in options if o["parcelas"] == sel.parcelas), None)
            if opt is None:
                return error_response(400, "Número de parcelas não ofertado")
            if sel.method not in opt["allowed_methods"]:
                return error_response(400, "Método não permitido para esta opção")

            provider = create_payment_provider()
            result = provider.create_charge(PaymentRequest(
                amount_cents=opt["valor_total_cents"], installments=sel.parcelas,
                method=sel.method, case_reference=str(case_id), organization_id=str(org),
                idempotency_key=sel.idempotency_key, schedule=opt["schedule"],
                mode=os.getenv("PAYMENT_MODE", "mock")))  # type: ignore[arg-type]

            payment = result.to_public()
            payment["idempotency_key"] = sel.idempotency_key
            payment["payload_hash"] = new_hash
            snapshot = {
                "version": 1, "pricing_config_version": iver,
                "selected_at": datetime.now(timezone.utc).isoformat(),
                "source_total_cents": total, "method": sel.method,
                "parcelas": opt["parcelas"], "has_juros": opt["has_juros"],
                "juros_mensal_bps": opt["juros_mensal_bps"],
                "valor_total_cents": opt["valor_total_cents"],
                "acrescimo_cents": opt["acrescimo_cents"], "currency": opt["currency"],
                "schedule": opt["schedule"], "payment": payment,
            }
            cur.execute(
                "UPDATE public.requests SET installment_plan = %s, payment_status = %s,"
                " pricing_config_version = %s, updated_at = now() WHERE id = %s",
                (Json(snapshot), result.status, iver, row["id"]))
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_PAYMENT_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao registrar pagamento")

    return success_response(201, "Pagamento registrado",
                            {"payment_status": result.status, "installment_plan": snapshot})


def _read_config(cur, org):
    cur.execute("SELECT installment_config, version FROM public.pricing_configs"
                " WHERE organization_id = %s", (org,))
    r = cur.fetchone()
    raw = (r["installment_config"] if r else None) or {}
    ver = r["version"] if r else 0
    try:
        return InstallmentConfig(**raw), ver
    except Exception:
        return InstallmentConfig(), ver
```

- [ ] **Step 4: Registrar a rota** em `serverless.yml` (após `runCaseTriage`):

```yaml
  createCasePayment:
    handler: src/handlers/payments.create_case_payment
    timeout: 15
    memorySize: 256
    events:
      - http:
          path: cases/{caseId}/payment
          method: post
```

- [ ] **Step 5: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_payments_handler.py -v`
Expected: PASS. Verificar rota local: reiniciar `local_server.py` e `curl -X POST .../api/v1/cases/<id>/payment`.

- [ ] **Step 6: Commit**

```bash
git add src/handlers/payments.py serverless.yml tests/test_payments_handler.py
git commit -m "feat(payment): POST /cases/{caseId}/payment (recalculo+idempotencia+mock)"
```

---

## Task 9: `cases.py` — expor pagamento no aggregate/detalhe

**Files:**
- Modify: `src/handlers/cases.py` (`get_case_aggregate` ~181, `_case_detail` ~492)
- Test: `tests/test_cases_handlers.py` (ou equivalente)

- [ ] **Step 1: Teste que falha**

```python
def test_aggregate_expoe_payment_status_e_plano():
    # criar caso + aplicar pagamento (reusar helpers), depois:
    data = _data(cases_h.get_case_aggregate(_event(admin, path={"caseId": case_id}), None))
    assert data["payment_status"] in ("pending", "simulated")
    assert "installment_plan" in data
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_cases_handlers.py::test_aggregate_expoe_payment_status_e_plano -v`
Expected: FAIL (`KeyError`).

- [ ] **Step 3: Implementar**

No `SELECT` que lê a `request` no aggregate/detalhe (`src/handlers/cases.py`), adicionar
`payment_status` e `installment_plan`, e incluí-los no dict de resposta:

```python
        cur.execute(
            "SELECT total_price_cents, price_snapshot, payment_status, installment_plan"
            " FROM public.requests WHERE case_id = %s", (case_id,))
        r = cur.fetchone()
        # ... no payload:
        payload["payment_status"] = r["payment_status"] if r else "pending"
        payload["installment_plan"] = r["installment_plan"] if r else None
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_cases_handlers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/handlers/cases.py tests/test_cases_handlers.py
git commit -m "feat(cases): expoe payment_status e installment_plan no caso"
```

---

## Task 10: Backend — suíte completa verde

- [ ] **Step 1:** Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: todos os testes passam (novos + regressão). Se algum teste antigo assumia a ausência das novas chaves, ajustar.

- [ ] **Step 2: Commit** (se houve ajuste de regressão)

```bash
git add tests/
git commit -m "test: alinhar suíte às novas chaves de pagamento"
```

---

## Task 11: Frontend — service (types + createCasePayment)

**Files:**
- Modify: `src/services/pricing.ts`, `src/services/cases.ts`

- [ ] **Step 1:** Em `src/services/pricing.ts`, estender os tipos:

```typescript
export interface InstallmentScheduleItem { numero: number; vencimento: string; valor_cents: number; }
export interface InstallmentOption {
  parcelas: number; has_juros: boolean; juros_mensal_bps: number;
  valor_parcela_cents: number; valor_total_cents: number; acrescimo_cents: number;
  currency: string; schedule: InstallmentScheduleItem[]; allowed_methods: string[];
}
export interface MethodRule { enabled: boolean; max_parcelas: number; }
export interface InstallmentConfig {
  enabled: boolean; max_parcelas: number; sem_juros_ate: number; juros_mensal_bps: number;
  valor_minimo_parcela_cents: number; primeiro_vencimento_dias: number;
  dia_vencimento: number | null; allowed_methods: Record<string, MethodRule>;
}
// PricingEstimate ganha: installment_options: InstallmentOption[]; pricing_config_version: number; payment_mode: string;
// PricingConfig/UpdatePricingConfigPayload ganham: installment_config?: InstallmentConfig;
```

- [ ] **Step 2:** Em `src/services/cases.ts`, adicionar:

```typescript
export interface CasePaymentPayload { parcelas: number; method: "pix" | "boleto" | "cartao";
  pricing_config_version?: number; idempotency_key: string; }
export async function createCasePayment(caseId: string, payload: CasePaymentPayload) {
  const res = await apiClient.post(`/api/v1/cases/${caseId}/payment`, payload);
  return res.data;
}
```

- [ ] **Step 3:** Run: `npm run typecheck && npm run lint`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add src/services/pricing.ts src/services/cases.ts
git commit -m "feat(front): types de installment + createCasePayment"
```

---

## Task 12: Frontend — `InstallmentConfigCard` no admin

**Files:**
- Create: `components/pricing/InstallmentConfigCard.tsx`
- Modify: `src/app/admin/pricing/page.tsx`

- [ ] **Step 1:** Criar `components/pricing/InstallmentConfigCard.tsx`: um `Card` com toggle `enabled`,
inputs numéricos (`max_parcelas`, `sem_juros_ate`, `primeiro_vencimento_dias`, `dia_vencimento`),
`CurrencyInput` para `valor_minimo_parcela_cents`, input de **taxa em %** convertida para bps
(`bps = Math.round(pct*100)`, `pct = bps/100`), e checkboxes de métodos (`allowed_methods`). Props:
`{ value: InstallmentConfig; onChange: (c: InstallmentConfig) => void; paymentMode: string }`.
Mostrar aviso de simulação quando `paymentMode === "mock"`. Reusar `CurrencyInput`/`centsToReaisLabel`.

- [ ] **Step 2:** Em `src/app/admin/pricing/page.tsx`: adicionar estado `installmentConfig`, carregar de
`cfg.installment_config` (com default `{enabled:false,...}`), renderizar `<InstallmentConfigCard>` e
incluir `installment_config` no `payload` do `handleSave`. Incluir no `hasChanges`.

- [ ] **Step 3:** Run: `npm run typecheck && npm run lint`
Expected: exit 0.

- [ ] **Step 4:** Verificação manual: `/admin/pricing` mostra o card; salvar persiste (ver `version` subir).

- [ ] **Step 5: Commit**

```bash
git add components/pricing/InstallmentConfigCard.tsx src/app/admin/pricing/page.tsx
git commit -m "feat(front): card de configuracao de parcelamento no admin"
```

---

## Task 13: Frontend — tela de pagamento `/cases/[id]/pagamento`

**Files:**
- Create: `src/app/cases/[id]/pagamento/page.tsx`

- [ ] **Step 1:** Criar a página (client component, dentro de `<AuthGuard><AppLayout>`):
  1. `getCaseAggregate(id)` para o total/estado; `estimatePricing` **não** é necessário se o aggregate
     já trouxer as opções — senão, chamar `getPricingCatalog`/estimate para obter `installment_options`
     do total do caso. (Simplification: reusar o endpoint de estimate com o produto do caso.)
  2. Seletor de método (Pix/Boleto/Cartão) filtrando `allowed_methods` da opção.
  3. Lista de opções (parcela/total/acréscimo) + cronograma da opção selecionada.
  4. `idempotency_key` gerado uma vez com `crypto.randomUUID()`; botão "Confirmar pagamento" com trava de
     duplo clique (`submitting`); chamar `createCasePayment(id, {parcelas, method, idempotency_key})`.
  5. Tratar 409 (mostrar aviso "pagamento já registrado / recarregue"); banner de simulação.
  6. Sucesso → `router.push(/cases/${id})`.

- [ ] **Step 2:** Run: `npm run typecheck && npm run lint`
Expected: exit 0.

- [ ] **Step 3:** Verificação manual: registrar um pedido → abrir `/cases/[id]/pagamento` → escolher 3x →
confirmar → volta ao caso com plano gravado.

- [ ] **Step 4: Commit**

```bash
git add "src/app/cases/[id]/pagamento/page.tsx"
git commit -m "feat(front): tela dedicada de pagamento do caso"
```

---

## Task 14: Frontend — badge + CTA no detalhe do caso

**Files:**
- Modify: `src/app/cases/[id]/page.tsx`

- [ ] **Step 1:** No detalhe do caso, ler `payment_status` + `installment_plan` do aggregate. Se
`pending`, mostrar badge "Pagamento pendente" + botão "Concluir pagamento" → `/cases/[id]/pagamento`.
Se configurado, mostrar um resumo (parcelas, total, método, status simulado) + cronograma. Nunca exibir
campos sensíveis.

- [ ] **Step 2:** Run: `npm run typecheck && npm run lint`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add "src/app/cases/[id]/page.tsx"
git commit -m "feat(front): status de pagamento + CTA no detalhe do caso"
```

---

## Task 15: Gate final + E2E manual

- [ ] **Step 1: Backend gate**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: tudo verde.

- [ ] **Step 2: Frontend gate**

Run: `npm run lint && npm run typecheck && npm run test && npm run build`
Expected: todos exit 0.

- [ ] **Step 3: E2E manual (Playwright ou navegador)**
  1. Admin habilita parcelamento (3x sem juros, 12x com juros) em `/admin/pricing`.
  2. Cria um pedido no wizard → cai no caso com "Pagamento pendente".
  3. Abre `/cases/[id]/pagamento`, escolhe 6x cartão → confirma → caso mostra plano/cronograma "simulado".
  4. Payload alterado com parcela inexistente → 400. Duplo clique → não duplica (idempotência).
  5. Logs do backend sem PII/`raw`.

- [ ] **Step 4:** (não commitar nada novo; apenas validação)

---

## Nota de pré-deploy (Fase 7)

Antes de subir para AWS: ligar `PAYMENT_GATE=hard` (triagem/relatório exigem `payment_status in
('simulated','paid')`), implementar `RealPaymentProvider`/webhook, e revisar trilha de auditoria de
`requests`. Ver §8/§9/§15 do spec V4.
