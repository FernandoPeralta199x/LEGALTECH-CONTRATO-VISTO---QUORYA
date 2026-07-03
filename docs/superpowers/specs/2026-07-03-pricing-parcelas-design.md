# Pricing — Sistema de Parcelas e Seam de Pagamento — Design V3

**Data:** 2026-07-03
**Status:** consolidado (V2 externo + correções ancoradas no repositório + achados da análise 2).
**Escopo atual:** admin configura regras → cliente escolhe no wizard → backend recalcula → pagamento **mock/simulado** → plano gravado no caso (cronograma completo).
**Escopo futuro preparado:** gateway real (sandbox/live) via `PaymentProvider`, webhooks e status de pagamento — sem redesenho.
**Abordagem:** A (JSONB-por-org + módulo puro) + seam de pagamento.

---

## 0. Como esta V3 se relaciona com a V2

A V3 **adota** da V2: `Decimal` no Price, remover `raw` do DTO público, `allowed_methods` por método, enum de status completo, separação `mock/sandbox/live`, validações duras, eco de `pricing_config_version`, aviso de simulação no front.

A V3 **ajusta** a V2 (ancorado no código real):

- **Idempotência:** reusar `requests.idempotency_key` + `requests_org_idempotency_uniq` que **já existem** (handler já dá 409). Não criar `payment_idempotency_key`.
- **Código de erro de validação:** **400** (convenção do repo; zero `422` hoje). Não usar 422.
- **Colunas novas mínimas:** só `payment_status` e `pricing_config_version` como colunas; o resto vive no `installment_plan` JSONB (evita denormalização a manter em sincronia).
- **Webhook:** fica **no papel** (seção 9). Não criar `payment_webhooks.py` agora (endpoint sem gateway = superfície morta).
- **Config-version desatualizada:** **soft** (recálculo silencioso + log), não 409-hard. 409-hard fica opcional/fase 2.
- **Gate de qualidade:** `pytest` (backend) + `eslint`/`tsc`/`tsx --test`/`build` (frontend). `ruff`/`mypy` **não** estão configurados — não são gate.

A V3 **acrescenta** (análise 2, ancorada em `requests.py`):

- **NEW-1:** provider posicionado para sair da transação quando for real (persist-pending → chamar fora da tx → atualizar). Mock inline agora.
- **NEW-3:** algoritmo de amortização Price explícito (laço saldo/juros/amortização).
- **NEW-4:** cálculo 100% em centavos com `Decimal` (sem cents→reais→cents).
- **NEW-5:** data-base do cronograma em `America/Sao_Paulo`.
- **NEW-6:** regra de roll-forward quando o 1º vencimento cairia no passado.
- **NEW-7:** reusar o `est` já calculado no `POST /requests`; ler `installment_config` na mesma query de `pricing_configs`.
- **NEW-8:** sanitização única (`to_public`) de `payment_form`.

---

## 1. Objetivo

Admin configura regras de parcelamento por organização; ao criar o caso, o cliente escolhe uma opção de
pagamento no wizard. Nesta fase o pagamento é **simulado** (sem cobrança real), mas o fluxo nasce
compatível com gateway real: backend oferta opções → cliente escolhe `{parcelas, method}` → backend
recalcula/valida server-side → chama `PaymentProvider.create_charge(...)` → grava snapshot no caso.
Trocar Mock→Real é só configurar env — o handler chamador não muda.

## 2. Princípios obrigatórios

- **Dinheiro em inteiros:** centavos (`*_cents`), juros em basis points (`*_bps`; 299 = 2,99% a.m.).
- **Sem `float` no domínio financeiro.** Price usa `Decimal` + `ROUND_HALF_UP`, **tudo em centavos**.
- **Soma exata:** `Σ schedule.valor_cents == valor_total_cents` (última parcela fecha o saldo).
- **Backend = fonte da verdade (CVS-008):** recalcula parcelas/juros/cronograma/métodos; nunca confia
  no valor do front.
- **Fail-safe:** config ausente/inválida/desabilitada ⇒ apenas `1x à vista`.
- **Simulação honesta:** com `PAYMENT_MODE=mock`, o front exibe aviso claro de que não há cobrança real.

## 3. Ambientes e env

```env
PAYMENT_PROVIDER=mock          # mock | pagarme | mercadopago | stripe ...
PAYMENT_MODE=mock              # mock | sandbox | live
PAYMENT_API_KEY=
PAYMENT_WEBHOOK_SECRET=
```

Travas: `mock` livre em dev; `mock` em produção bloqueado via `enforce_production_safety()` já existente
(estender, não criar flag paralela). `live`/`sandbox` exigem `PAYMENT_API_KEY`. `RealPaymentProvider`
incompleto falha **no config-check/startup**, nunca só no clique do cliente. Gateway real **não** é
chamado nesta fase.

## 4. Arquitetura e fluxo

```
Admin /admin/pricing ── PUT /pricing/config ──► pricing_configs.installment_config (+ version)

Wizard ── POST /pricing/estimate ──► { total_price_cents, pricing_config_version,
                                        payment_mode, installment_options[] }
       ── escolhe {parcelas, method} ──► POST /requests { installment, pricing_config_version,
                                                          idempotency_key }
            backend: reusa est → compute_installment_options → valida opção+método
                     (version divergente ⇒ recálculo silencioso + log)
            provider.create_charge(...)  ← seam (mock agora; fora da tx quando real)
            grava installment_plan (JSONB) + payment_status + pricing_config_version
Detalhe do caso ── exibe plano, cronograma, status simulado
```

## 5. Domínio puro — `src/services/pricing/installments.py`

Funções sem I/O. `compute_installment_options(total_cents: int, config: InstallmentConfig, reference_date: date) -> list[InstallmentOption]`.
`reference_date` default = data atual em `America/Sao_Paulo` (**NEW-5**).

### 5.1 Config (Pydantic, `extra="forbid"`)

```
enabled: bool = False
max_parcelas: int          # 1..24
sem_juros_ate: int         # 1..max_parcelas
juros_mensal_bps: int      # >= 0
valor_minimo_parcela_cents: int   # >= 0
primeiro_vencimento_dias: int     # 0..365
dia_vencimento: int | None        # 1..28
allowed_methods: dict[str, MethodRule]   # chaves ⊆ {pix,boleto,cartao}
```

`MethodRule = { enabled: bool, max_parcelas: int }`. Default recomendado:
`pix {enabled, max_parcelas:1}`, `boleto {enabled, max_parcelas:1}`, `cartao {enabled, max_parcelas:12}`.

**Validação cross-field (`model_validator`, NEW-9):** `sem_juros_ate <= max_parcelas`;
`allowed_methods[m].max_parcelas <= max_parcelas`; chaves ⊆ {pix,boleto,cartao}. Violação ⇒ erro de
validação ⇒ **400** no handler.

### 5.2 Sem juros (`N <= sem_juros_ate` ou `juros_mensal_bps == 0`)

```
valor_base = total_cents // N
resíduo    = total_cents - valor_base * N
parcelas 1..N-1 = valor_base ; parcela N = valor_base + resíduo
valor_total_cents = total_cents ; acrescimo_cents = 0
```

### 5.3 Com juros — tabela Price em `Decimal`, centavos (NEW-3, NEW-4)

```
i   = Decimal(juros_mensal_bps) / Decimal(10000)
PV  = Decimal(total_cents)                              # em centavos, sem dividir por 100
PMT = (PV * i / (Decimal(1) - (Decimal(1)+i) ** -N))
pmt_cents = int(PMT.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

# amortização (fecha exato):
saldo = total_cents
for k in 1..N:
    juros_k = int((Decimal(saldo)*i).quantize(Decimal("1"), ROUND_HALF_UP))
    if k < N:
        parcela_k = pmt_cents
        amort_k   = parcela_k - juros_k
        saldo    -= amort_k
    else:                      # última fecha o saldo
        parcela_k = saldo + juros_k
        saldo = 0
valor_total_cents = Σ parcela_k
acrescimo_cents   = valor_total_cents - total_cents
```

### 5.4 Filtro de valor mínimo

Se `N > 1` e a parcela `< valor_minimo_parcela_cents`, a opção é descartada. `1x` (à vista) sempre
existe, inclusive com `enabled=false`.

### 5.5 Cronograma (NEW-5, NEW-6)

```
base = reference_date + primeiro_vencimento_dias   # vencimento da parcela 1
parcela k vence em add_months(base, k-1)
se dia_vencimento != null: fixa o dia (clamp 28)
   se, após fixar, a data da parcela 1 < base  →  rola +1 mês (evita vencimento no passado)
```

`add_months`: helper local (sem dependência nova) que soma meses e faz clamp do dia.

### 5.6 Saída — `InstallmentOption`

```
parcelas, has_juros, juros_mensal_bps, valor_parcela_cents,
valor_total_cents, acrescimo_cents, currency="BRL",
schedule: list[{numero, vencimento (ISO date), valor_cents}],
allowed_methods: list[str]     # métodos válidos para esta opção
```

### 5.7 Casos-limite (testes obrigatórios)

`total_cents < 0` falha; `total_cents == 0` ⇒ só `1x` de 0; `enabled=false` ⇒ só `1x`; `N=1` sem juros;
`juros_mensal_bps=0` sem juros; `sem_juros_ate > max_parcelas` falha na validação; `dia_vencimento`
fora de 1..28 falha; `max_parcelas` fora de 1..24 falha; virada de ano no cronograma; `add_months` com
clamp de dia.

## 6. Seam de pagamento — `src/adapters/payment.py`

Espelha `procon.py`/`targetdata.py` (Protocol + Mock + Real placeholder + factory por env).

```python
@dataclass(frozen=True)
class PaymentRequest:
    amount_cents: int
    installments: int
    method: Literal["pix","boleto","cartao"]
    case_reference: str
    organization_id: str
    idempotency_key: str
    schedule: list[dict]
    currency: str = "BRL"
    mode: Literal["mock","sandbox","live"] = "mock"

@dataclass(frozen=True)
class PaymentResult:
    provider: str
    mode: Literal["mock","sandbox","live"]
    status: Literal["simulated","pending","paid","failed","canceled","expired","refunded"]
    method: Literal["pix","boleto","cartao"]
    external_reference: str | None
    payment_form: dict          # boleto{url,linha_digitavel} | pix{qr,copia_cola} | cartao{auth}
    requested_at: str           # ISO 8601
    def to_public(self) -> dict: ...   # NEW-8: remove campos sensíveis/raw; usado na API e no snapshot
```

`raw` do gateway **não** entra no DTO público, nem em log, nem em `installment_plan`. `PaymentProvider`
(Protocol) expõe `create_charge(PaymentRequest) -> PaymentResult`. `MockPaymentProvider` retorna
`status="simulated"` com `payment_form` fictício por método (e um modo de teste opcional para `failed`).
`RealPaymentProvider` = placeholder documentado (lê `PAYMENT_API_KEY`; `NotImplementedError` claro).
`create_payment_provider(...)` por env (`PAYMENT_PROVIDER`/`PAYMENT_MODE`; default mock).

**Guardrail de transação (NEW-1):** no Mock, `create_charge` é in-memory e pode rodar inline. O design
posiciona a persistência com `payment_status='pending'` de modo que, quando o provider virar HTTP real,
a chamada saia da transação (persist-pending → chamar fora da tx → atualizar status) **sem redesenho do
handler**.

## 7. Banco de dados — migration `017_installments.sql`

```sql
ALTER TABLE public.pricing_configs
  ADD COLUMN IF NOT EXISTS installment_config jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.requests
  ADD COLUMN IF NOT EXISTS installment_plan jsonb,          -- null = à vista
  ADD COLUMN IF NOT EXISTS payment_status text,             -- coluna consultável
  ADD COLUMN IF NOT EXISTS pricing_config_version integer;  -- coluna consultável
```

RLS herdada (sem policy nova). Demais campos operacionais (provider, mode, method,
external_reference) vivem dentro de `installment_plan.payment` (JSONB) — evita denormalização a manter
em sincronia. Idempotência reusa `requests.idempotency_key`/`requests_org_idempotency_uniq` existentes.

### 7.1 Snapshot em `installment_plan`

```json
{
  "version": 1,
  "pricing_config_version": 7,
  "quoted_at": "2026-07-03T10:00:00-03:00",
  "selected_at": "2026-07-03T10:02:00-03:00",
  "source_total_cents": 682911,
  "parcelas": 10, "method": "cartao",
  "has_juros": true, "juros_mensal_bps": 299,
  "valor_total_cents": 752300, "acrescimo_cents": 69389, "currency": "BRL",
  "schedule": [{ "numero": 1, "vencimento": "2026-08-10", "valor_cents": 75230 }],
  "payment": { "provider":"mock","mode":"mock","status":"simulated","method":"cartao",
               "external_reference":"mock_case_abc123",
               "payment_form": { "type":"cartao","authorization_code":"MOCK-AUTH-123" },
               "requested_at":"2026-07-03T10:02:10-03:00" }
}
```

Nunca no snapshot público: `raw`, token, API key, CPF/CNPJ desnecessário, payload externo, URL sensível.

## 8. Contrato de API

- **`GET /pricing/config`** — devolve `installment_config` (default `{enabled:false,...}`) + `version`.
- **`PUT /pricing/config`** (admin, 400 em erro) — patch parcial via `model_fields_set`; carrega atual →
  aplica patch → preenche defaults → **valida config final completa** → persiste → bump de version.
- **`POST /pricing/estimate`** — resposta ganha `installment_options[]`, `pricing_config_version`,
  `payment_mode`. Config desabilitada ⇒ `[1x]`. Opções calculadas de `total_price_cents` + config.
- **`POST /requests`** — aceita `installment: {parcelas, method} | null`, `pricing_config_version`,
  reusa a `idempotency_key` já existente. Handler (NEW-7): reusa o `est` já computado (linha 99) e lê
  `installment_config` na mesma query de `pricing_configs`; `compute_installment_options`; valida
  `parcelas` ofertada e `method` permitido (senão **400**); version divergente ⇒ recálculo silencioso +
  log (soft); `create_charge(...)`; grava `installment_plan` + `payment_status` + `pricing_config_version`.
  Duplicidade de `idempotency_key` mantém o **409** atual (replay-200 fica para a fase gateway).
- **Leitura do caso** — agregado devolve `installment_plan` + `payment_status` (+ flag de simulação).

### 8.1 Erros

`400` payload/parcela-não-ofertada/método-inválido/config-inválida · `401` não autenticado ·
`403` sem permissão (config é admin-only) · `409` idempotency duplicada (comportamento atual) ·
`500` erro interno. (Sem `422`; sem `409`-de-versão nesta fase.)

## 9. Webhook (desenho — NÃO implementar agora)

Quando o gateway entrar: `POST /payments/webhook/{provider}` em `src/handlers/payment_webhooks.py`:
validar assinatura (`PAYMENT_WEBHOOK_SECRET`) → evento idempotente → localizar
`external_reference` → atualizar `payment_status` → auditar → responder 2xx sem vazar payload. Tabela
futura `payment_events {id, provider, external_event_id, external_reference, status, received_at,
processed_at, payload_sanitized}`. **Nesta fase o arquivo/endpoint não é criado.**

## 10. Frontend

- **`components/pricing/InstallmentConfigCard.tsx`** (extraído de `/admin/pricing/page.tsx`): habilitar;
  max parcelas; sem juros até N; taxa (input **% ↔ bps**, NEW-10); parcela mínima; 1º vencimento; dia de
  vencimento; métodos permitidos; aviso de simulação se `payment_mode=mock`. Reusa `CurrencyInput`/
  `centsToReaisLabel` já existentes.
- **`components/cases/wizard/PaymentStep.tsx`**: opções do backend (parcela/total/acréscimo/taxa),
  cronograma, métodos permitidos, envia `idempotency_key`, trava duplo clique, banner de simulação.
- **Detalhe do caso**: parcelas, cronograma, status, método, provider, aviso se simulado; nunca exibe
  `raw`/token/chaves/dados sensíveis.

Texto obrigatório enquanto mock: *"Pagamento simulado para testes. Nenhuma cobrança real será gerada."*

## 11. Segurança e privacidade

Backend deriva org da sessão (não confia em `organization_id` do front); admin só altera a própria org;
cliente só lê o próprio caso; logs sem CPF/CNPJ/token/`payment_form`/`raw`; API key nunca vai ao front;
`payment_form` passa por `to_public()` antes de resposta/persistência.

## 12. Testes (meta: zero-falha; gate = pytest + eslint/tsc/tsx/build)

- **Domínio** (`tests/test_installments.py`): divisão exata; resíduo na última; Price com `Decimal` e
  laço de amortização; `Σschedule == valor_total`; `N=1`/`bps=0` sem juros; `total=0`⇒1x;
  `total<0` falha; limites de `max_parcelas`/`sem_juros_ate`/`dia_vencimento`; virada de ano; roll-forward
  do 1º vencimento; `add_months`.
- **Payment** (`tests/test_payment_adapter.py`): Mock `simulated` por método; `external_reference`
  fictícia; factory por env; Real placeholder falha claro; `to_public` remove sensíveis; sem exception
  vazada em `failed`.
- **Handlers** (`tests/test_pricing_handlers.py`, `tests/test_requests*.py`): estimate inclui opções +
  version + payment_mode; config parcial faz merge e valida final; config inválida ⇒ 400; request reusa
  est e grava plano; rejeita parcela/método inválido (400); version divergente ⇒ recálculo silencioso;
  idempotency duplicada ⇒ 409; não confia em `organization_id` do payload; não-admin não altera config;
  outra org não lê o `installment_plan`.
- **Frontend**: types do service; `InstallmentConfigCard` renderiza/envia; `PaymentStep` renderiza
  opções + aviso; método só aparece quando permitido; duplo clique não duplica; detalhe exibe plano;
  nenhum dado sensível na UI.
- **Segurança/logs**: sem PII/token/`payment_form`/`raw` em log; front não recebe API key; mock-em-prod
  bloqueado por `enforce_production_safety`.

## 13. Estrutura de arquivos

**Backend — criar:** `src/services/pricing/installments.py`, `src/adapters/payment.py`,
`migrations/017_installments.sql`, `tests/test_installments.py`, `tests/test_payment_adapter.py`.
**Backend — modificar:** `src/schemas/pricing_schemas.py`, `src/handlers/pricing.py`,
`src/handlers/requests.py`, `tests/test_pricing_handlers.py`, `tests/test_requests*.py`.
**Frontend — criar:** `components/pricing/InstallmentConfigCard.tsx`,
`components/cases/wizard/PaymentStep.tsx`.
**Frontend — modificar:** `src/services/pricing.ts`, `src/services/cases.ts`,
`src/app/admin/pricing/page.tsx`, `components/cases/wizard/NewCaseWizard.tsx`, detalhe do caso.
**Docs:** `.env.example`, `docs/installments.md`.
**NÃO criar agora:** `src/handlers/payment_webhooks.py` (só quando o gateway entrar).

## 14. Ordem de implementação

1. Domínio puro (`installments.py`) + testes financeiros. 2. `payment.py` (Mock + factory) + testes.
3. Migration 017. 4. Schemas Pydantic (config, opções, seleção). 5. `pricing.py` (config + estimate).
6. `requests.py` (reusa est, valida, provider, grava). 7. Leitura do caso. 8. Frontend
(config → wizard → detalhe) + aviso de simulação. 9. `pytest` + `eslint`/`tsc`/`tsx`/`build`.
10. Fluxo manual ponta a ponta.

## 15. Fora de escopo (nesta fase)

Cobrança/captura/reembolso reais; webhook ativo; `PAYMENT_MODE=live`; replay-200 de idempotência;
409-de-versão hard; tabelas normalizadas (`case_installment_plans`/`installment_schedule_items`);
regras de parcela por produto.
