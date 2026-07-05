# Cartão de Crédito — Formulário + Tokenização (mock dev-only, gateway-ready) — Design

**Data:** 2026-07-04
**Escopo atual:** formulário de cartão na tela de pagamento + **tokenização mock client-side** (dev-only).
**Regra inegociável (PCI):** PAN (número), CVV e validade **nunca** trafegam pelo nosso backend, nem vão
ao banco, nem a logs. Só um **token** opaco chega ao servidor.
**Base:** complementa `2026-07-03-pricing-parcelas-design.md` (parcelamento só no cartão). Revisado pelo
Codex (5 bloqueantes de segurança incorporados).

---

## 0. Decisão de arquitetura (IMPORTANTE)

O **formulário próprio** (nossos inputs de número/CVV/validade) é **exclusivo do mock local/dev** — serve
para testar a UX. **Em produção, o cartão DEVE usar hosted fields / iframe do gateway** (Pagar.me/Mercado
Pago/Stripe), mirando **PCI SAQ A** — PAN/CVV nunca tocam nosso DOM/JS. O contrato do backend
(`{card_token, ...}`) já serve para os dois modelos (o token é opaco). O que construímos agora é
**dev-only e bloqueado fora de dev**.

## 1. Fluxo (o cartão nunca toca o backend)

```
Navegador (tela de pagamento, método=cartão, PAYMENT_MODE=mock)
  captura número/validade/CVV/nome/CPF  ── valida (Luhn/MM-AA/CVV/CPF, só UX) ──►
  tokenizeCard(card)  [src/services/payment/tokenize.ts]
     mock: deriva last4+brand, gera token "tok_mock_<uuid>", DESCARTA os dados crus
     real (futuro): SDK/hosted-fields do gateway → token real (card NUNCA no nosso JS)
        │  devolve { token, last4, brand }   (nada cru)
        ▼
  createCasePayment(caseId, { parcelas, method:"cartao", card_token, card_last4, card_brand, card_holder })
        │  (SEM número/CVV/validade no payload)
        ▼
POST /cases/{caseId}/payment
  reserva atômica pending→processing → provider.create_charge(token) → grava snapshot allowlisted
```

## 2. Frontend

### 2.1 `components/cases/payment/CreditCardForm.tsx` (novo, dev-only)
Renderizado **só quando** `method === "cartao"` **e** `payment_mode === "mock"`. Campos: número (máscara
`#### #### #### ####`), validade `MM/AA`, CVV, nome do titular, CPF (máscara), seletor de parcelas.
Validação **apenas para UX** (a verdade é sempre do gateway): Luhn, MM/AA futuro, CVV 3–4 dígitos, CPF
válido (reusar validador existente do repo), nome não-vazio. Banner: "Pagamento simulado — dev".

**Regras de segurança do componente:**
- Estado dos campos crus vive **só** neste componente; **limpo** após tokenizar.
- **Nunca** `console.*`/`JSON.stringify` do estado do form; **nunca** `localStorage`/`sessionStorage`.
- Sem analytics/session-replay/3rd-party script nesta tela.
- `autocomplete` apropriado (`cc-number`, `cc-exp`, `cc-csc`) mas os valores não saem para o backend.

### 2.2 `src/services/payment/tokenize.ts` (novo — o seam)
```ts
type RawCard = { number: string; exp: string; cvv: string; holder: string; cpf: string };
type CardToken = { token: string; last4: string; brand: string };
export async function tokenizeCard(card: RawCard): Promise<CardToken>
```
- **Mock:** valida, deriva `last4` (últimos 4) + `brand` (do BIN), retorna `tok_mock_<uuid>`. Os dados
  crus **não saem da função** (sem rede, sem log). `last4`/`brand` são **hints de exibição** — o backend
  os trata como simulados, não como verdade.
- **Real (futuro):** substituir o corpo por `gateway.tokenize(...)` (SDK) **ou** o form vira hosted
  fields do gateway (recomendado). Assinatura e chamador **não mudam**.

### 2.3 Tela de pagamento (`src/app/cases/[id]/pagamento/page.tsx`)
Quando `method==="cartao"`: mostra o `CreditCardForm`; no submit chama `tokenizeCard` → `createCasePayment`
com o token. Erros de tokenização/cobrança exibem mensagem **genérica** ("Não foi possível validar o
cartão"), **nunca** o erro cru. Pix/Boleto seguem o fluxo atual (sem form de cartão).

## 3. Backend

### 3.1 Schema (`PaymentSelectionSchema`, `extra="forbid"`)
Acrescenta **opcionais**: `card_token`, `card_last4` (4 díg.), `card_brand`, `card_holder`.
Como o schema é `extra="forbid"`, **qualquer** campo cru (`card_number`, `cvv`, `card_cvv`, `exp`) →
**400** automático (defesa). `card_last4`/`brand`/`holder` são **hints**, não fonte de verdade.

### 3.2 Handler (`create_case_payment`)
- Se `method=="cartao"`: **exige** `card_token` (senão 400).
- **Anti-dupla-cobrança (Codex #2):** reserva atômica antes do provider —
  `UPDATE requests SET payment_status='processing' WHERE case_id=… AND payment_status='pending' AND installment_plan IS NULL`;
  `rowcount==0` ⇒ já em processamento/pago ⇒ replay idempotente (200) ou 409. Só então chama o provider
  (fora da tx). Sucesso ⇒ grava plano + status; falha do provider ⇒ **reverte** `processing→pending`.
- **Idempotência (Codex #2):** `payload_hash` server-side inclui `method`, `parcelas`,
  `valor_total_cents`, `currency`, `provider`, `mode` e `sha256(card_token)` — **nunca** PAN/CVV.
- **Metadados do cartão (Codex #3):** persiste `last4`/`brand` **retornados pelo provider**, não os do
  cliente. No mock, marcados `"simulated": true`.

### 3.3 Provider (`payment.py`)
`PaymentRequest` ganha `card_token: str | None` e `card_last4_hint`/`card_brand_hint`.
`MockPaymentProvider` (cartão): status `simulated`, `payment_form` = `{type:"cartao", brand, last4,
authorization_code:"MOCK-AUTH", simulated:true}` (a partir do hint). `RealPaymentProvider`: usa o token no
gateway, ignora hints, usa a resposta do gateway como verdade.

**`to_public()` — allowlist estrita por método (Codex #4):** só devolve campos conhecidos. Cartão:
`{type, brand, last4, authorization_code?, simulated?}`. **Nunca** raw/token/CVV/validade/CPF/mensagem
crua do gateway. Aplicada na API **e** antes de gravar no `installment_plan`.

### 3.4 Trava de mock fora de dev (`safety.py`, Codex #1)
Estender `enforce_production_safety`: em ambiente produtivo, `PAYMENT_PROVIDER=mock` **ou**
`PAYMENT_MODE=mock` ⇒ boot bloqueado; `sandbox`/`live` exigem `PAYMENT_API_KEY`.

## 4. Segurança — invariantes (checáveis por teste)
- Payload de `/payment` **nunca** contém `card_number`/`cvv`/`exp` (schema `extra="forbid"` → 400).
- Nada de PAN/CVV/validade em DB, log, `installment_plan`, timeline, auditoria ou `payment_form`.
- **CPF do titular não é persistido** (vai ao gateway de passagem, no real). Nome do titular só se
  necessário, mascarado, nunca em log/erro.
- `to_public()` é allowlist; erros de cartão são genéricos (sem `str(e)` do gateway na tela/log).
- Mock bloqueado fora de dev; token mock (`tok_mock_*`) é dev-only.

## 5. Testes
- **Front:** Luhn/MM-AA/CVV/CPF; `tokenizeCard` mock retorna `{token,last4,brand}` e **não** expõe os
  crus; o payload de `createCasePayment` **não** contém número/CVV; erro de tokenização vira mensagem
  genérica.
- **Back:** `card_number` no corpo → **400**; cartão sem `card_token` → **400**; snapshot só com
  `last4/brand/status/external_reference` (sem PAN/CVV/token); `payload_hash` muda com token diferente;
  reserva `processing` evita segunda chamada ao provider (rowcount 0 → 409/replay); `to_public` filtra
  campos desconhecidos; `enforce_production_safety` bloqueia `PAYMENT_MODE=mock` fora de dev.

## 6. Arquivos
**Frontend — criar:** `components/cases/payment/CreditCardForm.tsx`, `src/services/payment/tokenize.ts`,
testes. **Modificar:** `src/app/cases/[id]/pagamento/page.tsx`, `src/services/cases.ts`
(`CasePaymentPayload` += card fields), `src/services/pricing.ts` se necessário.
**Backend — modificar:** `src/schemas/pricing_schemas.py`, `src/handlers/payments.py`,
`src/adapters/payment.py` (PaymentRequest + `to_public` allowlist), `src/utils/safety.py`,
`tests/test_payments_handler.py`, `tests/test_payment_adapter.py`.

## 7. Fora de escopo agora / pré-produção (Fase 7)
Substituir o form próprio por **hosted fields/iframe** do gateway (SAQ A); tokenização real via SDK;
webhook assinado + idempotente; CSP forte + origem restrita na tela de pagamento; logs sanitizados em
API Gateway/Lambda/X-Ray; tabela `payment_attempts` se a reserva por coluna não bastar; varredura de
dependências. **Nenhum** desses entra no MVP local.
