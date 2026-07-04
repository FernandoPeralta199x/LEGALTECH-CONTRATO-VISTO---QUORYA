# Pricing — Sistema de Parcelas e Seam de Pagamento — Design V4

**Data:** 2026-07-03
**Status:** consolidado (V2 externo + correções ancoradas no repo + análise 2 + parecer Codex).
**Mudança central da V4:** **pagamento pós-registro**. O wizard só cria o pedido/caso; o caso nasce com
`payment_status='pending'`; uma **tela dedicada** (`/cases/[id]/pagamento` → `POST /cases/{caseId}/payment`)
coleta método + parcelas + cronograma e grava o plano. Pagamento simulado agora; gateway real depois.
**Abordagem:** A (JSONB-por-org + módulo puro) + seam de pagamento + subfluxo de pagamento retomável.

---

## 0. Evolução do design (V2 → V3 → V4)

**V3 adotou da V2:** `Decimal`/Price, sem `raw` no DTO público, `allowed_methods` por método, enum de
status, `mock/sandbox/live`, validações duras, eco de `pricing_config_version`, aviso de simulação.

**V3 ajustou (ancorado no repo):** 400 (não 422); só `payment_status`+`pricing_config_version` como
colunas novas; webhook no papel; `enforce_production_safety` em vez de flag; gate = pytest + eslint/tsc.

**V4 muda (parecer Codex, ancorado no código):**

- **Pagamento sai do wizard.** `POST /requests` **não** recebe `installment`; cria o caso com
  `payment_status='pending'`, `installment_plan=NULL`, `pricing_config_version=NULL`. Não quebra o
  orquestrador transacional existente nem o teste de idempotência reject-409.
- **Novo endpoint `POST /cases/{caseId}/payment`** (subfluxo retomável). Resolve `case.request_id` via
  RLS e atualiza `public.requests`.
- **`installment_plan = NULL` significa "pagamento não configurado"** (não "à vista"). Mesmo 1x grava
  snapshot.
- **Idempotência própria do pagamento** (chave + `payload_hash` em `installment_plan.payment`), separada
  do `requests_org_idempotency_uniq` (que continua exclusivo do registro do pedido).
- **Gate de pagamento na triagem/relatório:** decisão explícita — **soft** no MVP (não bloqueia),
  pronto para virar hard.
- **Guardrail NEW-1 resolvido de graça:** como o provider é chamado no endpoint pós-registro, ele
  **nunca** fica dentro da transação gigante do `POST /requests`.

---

## 1. Objetivo e fluxo

Admin configura parcelamento por organização. O cliente cria o pedido no wizard (vê o **valor
referencial**, sem escolher pagamento). Ao registrar, o caso nasce **pendente de pagamento**. Numa tela
dedicada, escolhe método + parcelas, vê o cronograma e confirma — o backend recalcula server-side, chama
`PaymentProvider.create_charge(...)` (mock agora) e grava o snapshot no caso. Trocar Mock→Real é só env.

```
Admin /admin/pricing ── PUT /pricing/config ──► pricing_configs.installment_config (+ version)

Wizard ── POST /requests ──► cria request+case (payment_status='pending', installment_plan=NULL)
       └─ router.push(/cases/{caseId})

Caso /cases/[id] ── badge "Pagamento pendente" + CTA "Concluir pagamento"
     └─► /cases/[id]/pagamento
            GET /pricing/estimate (ou total do caso) ──► installment_options[]
            escolhe {parcelas, method} ──► POST /cases/{caseId}/payment
                { parcelas, method, pricing_config_version?, idempotency_key }
            backend: resolve request via case → recalcula do total do caso → valida opção+método
                     → provider.create_charge (fora de qualquer tx longa) → grava plano + status
     └─ detalhe exibe plano, cronograma, status (simulado)
```

## 2. Princípios obrigatórios

- **Dinheiro em inteiros:** centavos (`*_cents`), juros em bps (299 = 2,99% a.m.).
- **Sem `float`;** Price em `Decimal`+`ROUND_HALF_UP`, tudo em centavos. `Σschedule == valor_total`.
- **Backend = fonte da verdade (CVS-008):** o pagamento aceita do front **só** `parcelas`, `method`,
  `pricing_config_version?`, `idempotency_key`. `amount`, `organization_id`, `case_id` interno, `status`
  e `provider` são derivados no backend. Nunca confiar em valor do cliente.
- **Fail-safe:** config ausente/inválida/desabilitada ⇒ ofertar só `1x à vista`.
- **Simulação honesta:** `PAYMENT_MODE=mock` ⇒ aviso claro no front; **sem fallback local** de pagamento
  (diferente do fallback do wizard) — nada pode parecer cobrança real.

## 3. Ambientes e env

```env
PAYMENT_PROVIDER=mock          # mock | pagarme | mercadopago | stripe ...
PAYMENT_MODE=mock              # mock | sandbox | live
PAYMENT_API_KEY=
PAYMENT_WEBHOOK_SECRET=
```

`mock` livre em dev; `mock` em produção bloqueado via `enforce_production_safety()` existente (estender).
`live`/`sandbox` exigem `PAYMENT_API_KEY`. `RealPaymentProvider` incompleto falha no config-check/startup,
nunca só no clique. Gateway real **não** é chamado nesta fase.

## 4. Domínio puro — `src/services/pricing/installments.py`

Funções sem I/O. `compute_installment_options(total_cents, config, reference_date) -> list[InstallmentOption]`.
`reference_date` default = data atual em `America/Sao_Paulo`.

### 4.1 Config (Pydantic, `extra="forbid"`)

```
enabled: bool = False
max_parcelas: int                 # 1..24
sem_juros_ate: int                # 1..max_parcelas
juros_mensal_bps: int             # >= 0
valor_minimo_parcela_cents: int   # >= 0
primeiro_vencimento_dias: int     # 0..365
dia_vencimento: int | None        # 1..28
allowed_methods: dict[str, MethodRule]   # chaves ⊆ {pix,boleto,cartao}
```

`MethodRule = { enabled: bool, max_parcelas: int }`. Default: `pix {on,1}`, `boleto {on,1}`,
`cartao {on,12}` (Pix/Boleto só 1x; Cartão parcela). `model_validator`: `sem_juros_ate <= max_parcelas`;
`allowed_methods[m].max_parcelas <= max_parcelas`; chaves ⊆ {pix,boleto,cartao} → violação = 400.

### 4.2 Sem juros (`N <= sem_juros_ate` ou `juros_mensal_bps == 0`)

`valor_base = total // N`; resíduo na última parcela; `valor_total = total`; `acrescimo = 0`.

### 4.3 Com juros — tabela Price em `Decimal`, centavos

```
i = Decimal(juros_mensal_bps)/Decimal(10000);  PV = Decimal(total_cents)
PMT = (PV * i / (Decimal(1) - (Decimal(1)+i) ** -N))
pmt_cents = int(PMT.quantize(Decimal("1"), ROUND_HALF_UP))
# amortização (fecha exato):
saldo = total_cents
for k in 1..N:
    juros_k = int((Decimal(saldo)*i).quantize(Decimal("1"), ROUND_HALF_UP))
    if k < N: parcela_k = pmt_cents; saldo -= (parcela_k - juros_k)
    else:     parcela_k = saldo + juros_k; saldo = 0
valor_total = Σ parcela_k;  acrescimo = valor_total - total_cents
```

### 4.4 Filtro / cronograma

Se `N>1` e parcela `< valor_minimo_parcela_cents`, descarta a opção (`1x` sempre existe).
`base = reference_date + primeiro_vencimento_dias`; parcela `k` em `add_months(base, k-1)`; se
`dia_vencimento` != null, fixa o dia (clamp 28); se a parcela 1 cair antes de `base`, rola +1 mês.
`add_months`: helper local, sem dependência nova.

### 4.5 Saída — `InstallmentOption`

`parcelas, has_juros, juros_mensal_bps, valor_parcela_cents, valor_total_cents, acrescimo_cents,
currency="BRL", schedule:[{numero,vencimento,valor_cents}], allowed_methods:list[str]`.

### 4.6 Casos-limite (testes)

`total<0` falha; `total==0`⇒1x de 0; `enabled=false`⇒1x; `N=1`/`bps=0` sem juros; limites de
`max_parcelas`/`sem_juros_ate`/`dia_vencimento`; virada de ano; roll-forward; `add_months`.

## 5. Seam de pagamento — `src/adapters/payment.py`

Espelha `procon.py` (Protocol + Mock + Real placeholder + factory por env).

```python
@dataclass(frozen=True)
class PaymentRequest:
    amount_cents: int; installments: int; method: Literal["pix","boleto","cartao"]
    case_reference: str; organization_id: str; idempotency_key: str
    schedule: list[dict]; currency: str = "BRL"; mode: Literal["mock","sandbox","live"] = "mock"

@dataclass(frozen=True)
class PaymentResult:
    provider: str; mode: Literal["mock","sandbox","live"]
    status: Literal["simulated","pending","paid","failed","canceled","expired","refunded"]
    method: Literal["pix","boleto","cartao"]; external_reference: str | None
    payment_form: dict; requested_at: str
    def to_public(self) -> dict: ...   # remove sensível/raw; usado na API e no snapshot
```

`raw` do gateway não entra em DTO público, log nem `installment_plan`. `MockPaymentProvider` →
`status="simulated"` + `payment_form` fictício por método (modo de teste opcional p/ `failed`).
`RealPaymentProvider` = placeholder documentado. `create_payment_provider(...)` por env.

## 6. Banco de dados — migration `017_installments.sql`

```sql
ALTER TABLE public.pricing_configs
  ADD COLUMN IF NOT EXISTS installment_config jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.requests
  ADD COLUMN IF NOT EXISTS installment_plan jsonb,          -- NULL = pagamento não configurado
  ADD COLUMN IF NOT EXISTS payment_status text DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS pricing_config_version integer;
```

RLS herdada. `installment_plan=NULL` ⇒ **não configurado** (mesmo 1x à vista grava snapshot). Provider,
mode, method, external_reference e a idempotência do pagamento vivem em `installment_plan.payment`.

### 6.1 Snapshot em `installment_plan`

```json
{ "version":1, "pricing_config_version":7,
  "quoted_at":"2026-07-03T10:00:00-03:00", "selected_at":"2026-07-03T10:02:00-03:00",
  "source_total_cents":23700, "parcelas":6, "method":"cartao",
  "has_juros":true, "juros_mensal_bps":299, "valor_total_cents":26238, "acrescimo_cents":2538,
  "currency":"BRL", "schedule":[{"numero":1,"vencimento":"2026-08-10","valor_cents":4373}],
  "payment": { "provider":"mock","mode":"mock","status":"simulated","method":"cartao",
               "external_reference":"mock_case_abc123",
               "idempotency_key":"uuid","payload_hash":"sha256:...",
               "payment_form":{"type":"cartao","authorization_code":"MOCK-AUTH-123"},
               "requested_at":"2026-07-03T10:02:10-03:00" } }
```

Nunca no snapshot público: `raw`, token, API key, CPF/CNPJ desnecessário, payload externo, URL sensível.

## 7. Contrato de API

- **`GET /pricing/config`** — devolve `installment_config` (default `{enabled:false,...}`) + `version`.
- **`PUT /pricing/config`** (admin, 400 em erro) — patch parcial → aplica → defaults → **valida config
  final completa** → persiste → bump de version.
- **`POST /pricing/estimate`** — resposta ganha `installment_options[]`, `pricing_config_version`,
  `payment_mode`. Config desabilitada ⇒ `[1x]`.
- **`POST /requests`** — **inalterado no contrato de entrada** (não recebe `installment`). Passa a gravar
  `payment_status='pending'`, `installment_plan=NULL`, `pricing_config_version=NULL`. Mantém o **409** de
  idempotência de criação (`requests_org_idempotency_uniq`).
- **`POST /cases/{caseId}/payment`** (novo) — corpo: `{ parcelas, method, pricing_config_version?,
  idempotency_key }`. Handler `src/handlers/payments.py`:
  1. resolve o `request` pelo `case_id` (RLS); lê `total_price_cents`/`price_snapshot`;
  2. **idempotência do pagamento:** se já há `installment_plan.payment` → mesma `idempotency_key` +
     mesmo `payload_hash` devolve o snapshot atual (replay 200); mesma chave + payload diferente ⇒ 409;
     status já `simulated/paid` ⇒ recusa nova cobrança (409);
  3. lê `installment_config`; `compute_installment_options` do total do caso; valida `parcelas` ofertada
     e `method` permitido (senão 400); version divergente ⇒ recálculo silencioso + log (soft);
  4. `create_payment_provider().create_charge(...)` (fora de qualquer transação longa);
  5. grava `installment_plan` (com `payment.to_public()`) + `payment_status` + `pricing_config_version`.
- **Leitura do caso** — `get_case_aggregate`/`_case_detail` passam a expor `payment_status` +
  `installment_plan` (bloco de pagamento). `pending` sem `payment.requested_at` = "sem tentativa";
  `pending` com `external_reference` = "aguardando" (boleto/Pix, fase gateway).

### 7.1 Erros

`400` payload/parcela/method/config inválidos · `401` não autenticado · `403` sem permissão ·
`404` caso inexistente/fora da org · `409` idempotência (criação do pedido; ou replay divergente/já-pago
no pagamento) · `500` interno. Sem `422`; sem 409-de-versão nesta fase.

## 8. Gate de pagamento na triagem/relatório (decisão)

Com o pagamento deferível, `run_triage` e a geração de relatório podem rodar sem pagamento.
**Decisão: soft no MVP local, HARD antes de subir para AWS (Fase 7).** Agora **não** bloquear (pagamento
é simulado; travar forçaria "pagar" a cada teste), mas expor `payment_status` na UI. O gate nasce como um
**único ponto de checagem** com flag (`PAYMENT_GATE=soft|hard`, default `soft`): ao virar `hard`,
`run_triage` e a geração de relatório exigem `payment_status in ('simulated','paid')` (ou exceção admin
explícita). **Antes do deploy AWS, ligar `hard`.** Mesma filosofia do 409-de-versão. Incluir no checklist
de pré-deploy (Fase 7).

## 9. Webhook (desenho — NÃO implementar agora)

`POST /payments/webhook/{provider}` em `src/handlers/payment_webhooks.py` (fase gateway): validar
assinatura → evento idempotente → localizar `external_reference` → atualizar `payment_status` → auditar
→ 2xx sem vazar payload. Tabela futura `payment_events`. **Arquivo/endpoint não criados nesta fase.**

## 10. Frontend

- **`components/pricing/InstallmentConfigCard.tsx`** (extraído de `/admin/pricing/page.tsx`): habilitar,
  max parcelas, sem juros até N, taxa (**% ↔ bps**), parcela mínima, 1º vencimento, dia, métodos
  permitidos, aviso de simulação. Reusa `CurrencyInput`/`centsToReaisLabel`.
- **Wizard** (`NewCaseWizard.tsx`): **sem** escolha de pagamento; mantém `EstimateCard` (valor
  referencial). "Registrar pedido" → `router.push(/cases/{caseId})` (comportamento atual preservado).
- **`src/app/cases/[id]/pagamento/page.tsx`** (nova tela): opções do backend (parcela/total/acréscimo/
  taxa), cronograma, métodos permitidos, gera/enviar `idempotency_key`, trava duplo clique, trata 409,
  banner de simulação. Chama `createCasePayment(caseId, {parcelas, method})` (novo em `src/services/cases.ts`).
- **Detalhe do caso** (`src/app/cases/[id]/page.tsx`): badge de `payment_status` + CTA "Concluir
  pagamento" quando `pending`; quando configurado, exibe plano/cronograma/método/status. Nunca exibe
  `raw`/token/chaves.

Texto obrigatório enquanto mock: *"Pagamento simulado para testes. Nenhuma cobrança real será gerada."*

## 11. Segurança e privacidade

Backend deriva org da sessão (não confia em `organization_id` do front); pagamento aceita só
`parcelas`/`method`/idempotência; admin só altera a própria org; cliente só lê/paga o próprio caso; logs
sem CPF/CNPJ/token/`payment_form`/`raw`; API key nunca vai ao front; `payment_form` passa por
`to_public()`. **Sem fallback local de pagamento.** Nota: updates em `requests` podem não estar cobertos
pelos triggers de auditoria atuais — para dinheiro real, prever trilha/`payment_events` (fase gateway).

## 12. Testes (gate = pytest + eslint/tsc/tsx/build)

- **Domínio** (`tests/test_installments.py`): divisão exata; resíduo na última; Price/`Decimal` com laço;
  `Σschedule==valor_total`; `N=1`/`bps=0` sem juros; `total=0`⇒1x; `total<0` falha; limites; virada de
  ano; roll-forward; `add_months`.
- **Payment adapter** (`tests/test_payment_adapter.py`): Mock `simulated` por método;
  `external_reference` fictícia; factory por env; Real placeholder falha claro; `to_public` remove
  sensíveis; `failed` sem exception vazada.
- **Handlers**: estimate inclui opções+version+mode; config parcial faz merge e valida final (400 se
  inválida); `POST /requests` grava `payment_status='pending'`+`installment_plan=NULL` e mantém 409;
  `POST /cases/{id}/payment` recalcula do total do caso, grava plano, rejeita parcela/método inválido
  (400), replay idempotente (mesma chave+payload ⇒ mesmo snapshot; divergente ⇒ 409; já-pago ⇒ 409),
  não confia em `organization_id`/`amount` do payload; caso de outra org ⇒ 404; não-admin não altera
  config.
- **Frontend**: `InstallmentConfigCard` renderiza/envia; tela `/cases/[id]/pagamento` renderiza opções +
  aviso; método só quando permitido; duplo clique não duplica; detalhe exibe status + CTA; nenhum dado
  sensível.
- **Segurança/logs**: sem PII/token/`payment_form`/`raw`; front não recebe API key; mock-em-prod
  bloqueado; sem fallback local de pagamento.

## 13. Estrutura de arquivos

**Backend — criar:** `src/services/pricing/installments.py`, `src/adapters/payment.py`,
`src/handlers/payments.py` (endpoint `POST /cases/{caseId}/payment`), `migrations/017_installments.sql`,
`tests/test_installments.py`, `tests/test_payment_adapter.py`, `tests/test_payments_handler.py`.
**Backend — modificar:** `src/schemas/pricing_schemas.py` (config + opções + `PaymentSelectionSchema`),
`src/handlers/pricing.py` (estimate + config), `src/handlers/requests.py` (grava `payment_status`/
`installment_plan`/`pricing_config_version` no insert; **não** muda o contrato de entrada),
`src/handlers/cases.py` (expor pagamento no aggregate/detalhe), `serverless.yml` + `tools/local_server.py`
(registrar a rota `cases/{caseId}/payment`), `tests/test_pricing_handlers.py`, `tests/test_requests*.py`,
`tests/test_cases*.py`.
**Frontend — criar:** `components/pricing/InstallmentConfigCard.tsx`,
`src/app/cases/[id]/pagamento/page.tsx`.
**Frontend — modificar:** `src/services/pricing.ts`, `src/services/cases.ts` (`createCasePayment`),
`src/app/admin/pricing/page.tsx`, `src/app/cases/[id]/page.tsx` (badge + CTA).
**Docs:** `.env.example`, `docs/installments.md`.
**NÃO criar agora:** `src/handlers/payment_webhooks.py`.

## 14. Ordem de implementação

1. Domínio (`installments.py`) + testes financeiros. 2. `payment.py` (Mock+factory) + testes.
3. Migration 017. 4. Schemas (config, opções, seleção). 5. `pricing.py` (config+estimate).
6. `requests.py` (grava status pending, sem mudar entrada). 7. `payments.py` + rota (serverless+local) +
testes. 8. `cases.py` expõe pagamento. 9. Frontend: config → tela de pagamento → detalhe (badge+CTA) +
aviso. 10. pytest + eslint/tsc/tsx/build. 11. Fluxo manual ponta a ponta.

## 15. Fora de escopo (nesta fase)

Cobrança/captura/reembolso reais; webhook ativo; `PAYMENT_MODE=live`; gate hard de triagem;
409-de-versão hard; tabelas normalizadas; regras de parcela por produto; trilha de auditoria de
pagamento (payment_events).
