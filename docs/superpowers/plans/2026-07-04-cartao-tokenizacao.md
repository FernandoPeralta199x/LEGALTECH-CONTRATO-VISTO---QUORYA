# Cartão de Crédito — Formulário + Tokenização (mock) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formulário de cartão na tela de pagamento com tokenização mock client-side (dev-only), com o cartão nunca tocando o backend/DB (PCI), pronto para o gateway real.

**Architecture:** `tokenizeCard` (client) descarta os dados crus e devolve `token+last4+brand`; o backend recebe só o token (schema `extra="forbid"`), grava metadados do provider (allowlist), com reserva atômica anti-dupla-cobrança e idempotência que inclui `hash(card_token)`. Mock bloqueado fora de dev.

**Tech Stack:** Python serverless (pytest, PG18 `cv-pg18`:5433, venv `.venv`); Next.js 16 + TS (eslint/tsc/`tsx --test`/build).

**Spec:** `docs/superpowers/specs/2026-07-04-cartao-tokenizacao-design.md`.

**Convenções:** testes handler = `_event/_data/_admin_conn/seed_case_and_config` (ver `tests/test_payments_handler.py`); rodar `./.venv/Scripts/python.exe -m pytest <arq> -v`; 400 para validação; dinheiro em centavos.

---

## Mapa de arquivos
**Backend — modificar:** `src/utils/safety.py`, `src/schemas/pricing_schemas.py`, `src/adapters/payment.py`, `src/handlers/payments.py`, `tests/test_payment_adapter.py`, `tests/test_payments_handler.py`, `tests/test_safety.py` (criar se não existir).
**Frontend — criar:** `src/services/payment/tokenize.ts`, `src/services/payment/tokenize.test.ts`, `components/cases/payment/CreditCardForm.tsx`. **Modificar:** `src/services/cases.ts`, `src/app/cases/[id]/pagamento/page.tsx`.

Ordem: safety → schema+provider(to_public/allowlist) → handler(token+reserva+idempotência) → tokenize → form → tela+service → gate.

---

## Task 1: `safety.py` — bloquear mock de pagamento fora de dev

**Files:** Modify `src/utils/safety.py`; Test `tests/test_safety.py`

- [ ] **Step 1: Teste que falha**

```python
# tests/test_safety.py
import pytest
from src.utils.safety import enforce_production_safety

def _prod_env(monkeypatch, **over):
    base = {"ENVIRONMENT": "prod", "JWT_SECRET_KEY": "x" * 40,
            "AI_ANALYSIS_BACKEND": "real", "EMAIL_BACKEND": "ses",
            "STORAGE_BACKEND": "s3", "EMBEDDINGS_BACKEND": "real", "OCR_BACKEND": "real",
            "PAYMENT_PROVIDER": "pagarme", "PAYMENT_MODE": "live", "PAYMENT_API_KEY": "k"}
    base.update(over)
    for k, v in base.items():
        monkeypatch.setenv(k, v)

def test_bloqueia_payment_mock_em_producao(monkeypatch):
    _prod_env(monkeypatch, PAYMENT_MODE="mock")
    with pytest.raises(RuntimeError, match="PAYMENT"):
        enforce_production_safety()

def test_bloqueia_provider_mock_em_producao(monkeypatch):
    _prod_env(monkeypatch, PAYMENT_PROVIDER="mock")
    with pytest.raises(RuntimeError, match="PAYMENT"):
        enforce_production_safety()

def test_exige_api_key_para_gateway_real(monkeypatch):
    _prod_env(monkeypatch, PAYMENT_API_KEY="")
    with pytest.raises(RuntimeError, match="PAYMENT_API_KEY"):
        enforce_production_safety()

def test_prod_valido_nao_bloqueia(monkeypatch):
    _prod_env(monkeypatch)
    enforce_production_safety()  # não levanta
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_safety.py -v`
Expected: FAIL (o mock de pagamento hoje não é checado).

- [ ] **Step 3: Implementar** — em `enforce_production_safety`, antes do `if violations:`:

```python
    payment_provider = os.getenv("PAYMENT_PROVIDER", "mock")
    payment_mode = os.getenv("PAYMENT_MODE", "mock")
    if payment_provider == "mock" or payment_mode == "mock":
        violations.append("PAYMENT_PROVIDER/PAYMENT_MODE=mock em ambiente produtivo")
    elif not os.getenv("PAYMENT_API_KEY"):
        violations.append("PAYMENT_API_KEY ausente para gateway real (sandbox/live)")
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_safety.py -v`
Expected: PASS (4).

- [ ] **Step 5: Commit**

```bash
git add src/utils/safety.py tests/test_safety.py
git commit -m "feat(safety): bloqueia mock de pagamento fora de dev"
```

---

## Task 2: schema + provider (campos de cartão + `to_public` allowlist)

**Files:** Modify `src/schemas/pricing_schemas.py`, `src/adapters/payment.py`; Test `tests/test_payment_adapter.py`

- [ ] **Step 1: Teste que falha** (em `tests/test_payment_adapter.py`)

```python
def test_to_public_allowlist_cartao_remove_desconhecidos():
    from src.adapters.payment import PaymentResult
    r = PaymentResult(provider="mock", mode="mock", status="simulated", method="cartao",
                      external_reference="mock_x",
                      payment_form={"type": "cartao", "brand": "visa", "last4": "1234",
                                    "authorization_code": "A", "simulated": True,
                                    "card_number": "4111111111111111", "cvv": "123",
                                    "token": "tok_secret", "cpf": "060..."})
    pub = r.to_public()
    keys = set(pub["payment_form"].keys())
    assert keys <= {"type", "brand", "last4", "authorization_code", "simulated"}
    assert "card_number" not in keys and "cvv" not in keys and "token" not in keys

def test_mock_cartao_usa_hints_e_marca_simulated():
    from src.adapters.payment import PaymentRequest, MockPaymentProvider
    req = PaymentRequest(amount_cents=10000, installments=3, method="cartao",
                         case_reference="c1", organization_id="o1", idempotency_key="k",
                         schedule=[], card_token="tok_mock_1", card_last4_hint="4242",
                         card_brand_hint="visa")
    res = MockPaymentProvider().create_charge(req)
    pub = res.to_public()["payment_form"]
    assert pub["last4"] == "4242" and pub["brand"] == "visa" and pub["simulated"] is True
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_payment_adapter.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar**

Em `src/schemas/pricing_schemas.py`, `PaymentSelectionSchema` ganha os campos (mantém `extra="forbid"`):

```python
from pydantic import model_validator

class PaymentSelectionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parcelas: int = Field(ge=1, le=24)
    method: Literal["pix", "boleto", "cartao"]
    pricing_config_version: int | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)
    # Cartão: só o token + hints de exibição. NUNCA número/CVV/validade (extra=forbid rejeita).
    card_token: str | None = Field(default=None, max_length=255)
    card_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    card_brand: str | None = Field(default=None, max_length=20)
    card_holder: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _cartao_exige_token(self) -> "PaymentSelectionSchema":
        if self.method == "cartao" and not self.card_token:
            raise ValueError("cartão exige card_token (tokenização client-side)")
        return self
```

Em `src/adapters/payment.py`: `PaymentRequest` ganha `card_token`/`card_last4_hint`/`card_brand_hint`
(default None); `_mock_form("cartao", req)` usa os hints; `to_public` vira allowlist:

```python
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
    card_token: str | None = None
    card_last4_hint: str | None = None
    card_brand_hint: str | None = None

_PUBLIC_FORM_KEYS = {
    "pix": ("type", "qr_code", "copia_cola"),
    "boleto": ("type", "url", "linha_digitavel"),
    "cartao": ("type", "brand", "last4", "authorization_code", "simulated"),
}
# em PaymentResult.to_public():
    allowed = _PUBLIC_FORM_KEYS.get(self.method, ("type",))
    safe_form = {k: v for k, v in self.payment_form.items() if k in allowed}
    return {"provider": self.provider, "mode": self.mode, "status": self.status,
            "method": self.method, "external_reference": self.external_reference,
            "payment_form": safe_form, "requested_at": self.requested_at}
```

`MockPaymentProvider.create_charge` para cartão:

```python
    def create_charge(self, req: PaymentRequest) -> PaymentResult:
        form = _mock_form(req.method)
        if req.method == "cartao":
            form = {"type": "cartao", "brand": req.card_brand_hint or "desconhecida",
                    "last4": req.card_last4_hint or "0000",
                    "authorization_code": "MOCK-AUTH-123", "simulated": True}
        return PaymentResult(provider="mock", mode="mock", status="simulated",
                             method=req.method,
                             external_reference=f"mock_{req.case_reference}_{uuid.uuid4().hex[:8]}",
                             payment_form=form)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_payment_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/schemas/pricing_schemas.py src/adapters/payment.py tests/test_payment_adapter.py
git commit -m "feat(payment): campos de cartao (token+hints) + to_public allowlist"
```

---

## Task 3: `payments.py` — token, idempotência com hash(token), reserva anti-dupla-cobrança

**Files:** Modify `src/handlers/payments.py`; Test `tests/test_payments_handler.py`

- [ ] **Step 1: Testes que falham**

```python
def test_cartao_sem_token_400(seed_case_and_config):
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 3, "method": "cartao", "idempotency_key": "c1"}, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 400

def test_campo_cru_de_cartao_e_rejeitado_400(seed_case_and_config):
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 3, "method": "cartao", "idempotency_key": "c2",
        "card_token": "tok_mock_1", "card_number": "4111111111111111"},
        path={"caseId": case_id}), None)
    assert resp["statusCode"] == 400  # extra=forbid

def test_cartao_grava_last4_sem_dado_sensivel(seed_case_and_config):
    case_id, admin = seed_case_and_config
    resp = pay_h.create_case_payment(_event(admin, body={
        "parcelas": 3, "method": "cartao", "idempotency_key": "c3",
        "card_token": "tok_mock_1", "card_last4": "4242", "card_brand": "visa"},
        path={"caseId": case_id}), None)
    data = _data(resp)
    pay = data["installment_plan"]["payment"]
    blob = json.dumps(data["installment_plan"])
    assert pay["payment_form"]["last4"] == "4242"
    assert "card_token" not in blob and "tok_mock_1" not in blob and "cvv" not in blob
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_payments_handler.py -k cartao -v`
Expected: FAIL.

- [ ] **Step 3: Implementar** — em `src/handlers/payments.py`:

`_payload_hash` passa a incluir o token:

```python
def _payload_hash(sel: PaymentSelectionSchema) -> str:
    parts = {"parcelas": sel.parcelas, "method": sel.method}
    if sel.card_token:
        parts["card_fp"] = hashlib.sha256(sel.card_token.encode()).hexdigest()[:16]
    raw = json.dumps(parts, sort_keys=True)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:32]
```

No corpo do handler, após validar a opção/método e ANTES de chamar o provider, trocar a gravação por
reserva atômica + finalização:

```python
        # ── reserva atômica (anti-dupla-cobrança): só um request passa de pending->processing ──
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute(
                "UPDATE public.requests SET payment_status='processing', updated_at=now()"
                " WHERE id=%s AND payment_status='pending' AND installment_plan IS NULL",
                (request_id,))
            if cur.rowcount == 0:
                return error_response(409, "Pagamento já em processamento ou registrado")

        provider = create_payment_provider()
        try:
            result = provider.create_charge(PaymentRequest(
                amount_cents=opt["valor_total_cents"], installments=sel.parcelas,
                method=sel.method, case_reference=str(case_id), organization_id=str(org),
                idempotency_key=sel.idempotency_key, schedule=opt["schedule"],
                mode=os.getenv("PAYMENT_MODE", "mock"), card_token=sel.card_token,
                card_last4_hint=sel.card_last4, card_brand_hint=sel.card_brand))
        except Exception:
            with tenant_tx(user["user_id"], user["role"], org) as cur:  # libera a reserva
                cur.execute("UPDATE public.requests SET payment_status='pending', updated_at=now()"
                            " WHERE id=%s AND payment_status='processing'", (request_id,))
            raise

        payment = result.to_public()   # allowlisted; NÃO inclui card_token/last4 do cliente cru
        payment["idempotency_key"] = sel.idempotency_key
        payment["payload_hash"] = new_hash
        snapshot = { ... , "payment": payment }   # last4/brand vêm de payment["payment_form"] (provider)

        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute(
                "UPDATE public.requests SET installment_plan=%s, payment_status=%s,"
                " pricing_config_version=%s, updated_at=now()"
                " WHERE id=%s AND payment_status='processing'",
                (Json(snapshot), result.status, iver, request_id))
            if cur.rowcount == 0:
                return error_response(409, "Pagamento já registrado (concorrência)")
```

(O snapshot **não** deve conter `sel.card_token` nem `sel.card_holder`; `last4`/`brand` só via
`payment["payment_form"]`. `card_holder`/CPF **não** são persistidos.)

- [ ] **Step 4: Rodar e ver passar**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_payments_handler.py -v`
Expected: PASS (novos + existentes de idempotência/replay).

- [ ] **Step 5: Commit**

```bash
git add src/handlers/payments.py tests/test_payments_handler.py
git commit -m "feat(payment): cartao via token + reserva anti-dupla-cobranca + idempotencia com hash(token)"
```

---

## Task 4: `tokenize.ts` — tokenização mock (Luhn/last4/brand), descarta os crus

**Files:** Create `src/services/payment/tokenize.ts`, `src/services/payment/tokenize.test.ts`

- [ ] **Step 1: Teste que falha**

```ts
// src/services/payment/tokenize.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { tokenizeCard, luhnValid, cardBrand } from "./tokenize";

test("luhn valida número correto e rejeita errado", () => {
  assert.equal(luhnValid("4242424242424242"), true);
  assert.equal(luhnValid("4242424242424241"), false);
});

test("cardBrand detecta bandeira pelo BIN", () => {
  assert.equal(cardBrand("4242424242424242"), "visa");
  assert.equal(cardBrand("5555555555554444"), "mastercard");
});

test("tokenizeCard mock devolve token+last4+brand e NÃO expõe os crus", async () => {
  const r = await tokenizeCard({
    number: "4242 4242 4242 4242", exp: "12/30", cvv: "123",
    holder: "Fulano", cpf: "060.380.601-54"
  });
  assert.match(r.token, /^tok_mock_/);
  assert.equal(r.last4, "4242");
  assert.equal(r.brand, "visa");
  const blob = JSON.stringify(r);
  assert.ok(!blob.includes("4242424242424242") && !blob.includes("123"));
});

test("tokenizeCard rejeita número inválido", async () => {
  await assert.rejects(() => tokenizeCard({
    number: "1234 5678 9012 3456", exp: "12/30", cvv: "123", holder: "X", cpf: "060.380.601-54"
  }));
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run (na raiz do frontend): `npx tsx --test src/services/payment/tokenize.test.ts`
Expected: FAIL (módulo não existe).

- [ ] **Step 3: Implementar**

```ts
// src/services/payment/tokenize.ts
import { isValidCpf } from "@/lib/cpfCnpj";

export type RawCard = { number: string; exp: string; cvv: string; holder: string; cpf: string };
export type CardToken = { token: string; last4: string; brand: string };

export function luhnValid(digits: string): boolean {
  const n = digits.replace(/\D/g, "");
  if (n.length < 13 || n.length > 19) return false;
  let sum = 0;
  let alt = false;
  for (let i = n.length - 1; i >= 0; i--) {
    let d = Number(n[i]);
    if (alt) { d *= 2; if (d > 9) d -= 9; }
    sum += d;
    alt = !alt;
  }
  return sum % 10 === 0;
}

export function cardBrand(digits: string): string {
  const n = digits.replace(/\D/g, "");
  if (/^4/.test(n)) return "visa";
  if (/^5[1-5]/.test(n) || /^2(2[2-9]|[3-6]|7[01]|720)/.test(n)) return "mastercard";
  if (/^3[47]/.test(n)) return "amex";
  if (/^(4011|4312|4389|5041|5066|5067|509|6277|6362|6363|650|6516|6550)/.test(n)) return "elo";
  return "desconhecida";
}

function validExp(exp: string): boolean {
  const m = /^(\d{2})\/(\d{2})$/.exec(exp.trim());
  if (!m) return false;
  const month = Number(m[1]);
  const year = 2000 + Number(m[2]);
  if (month < 1 || month > 12) return false;
  const last = new Date(year, month, 0, 23, 59, 59);
  return last.getTime() >= Date.now();
}

/**
 * MOCK dev-only: valida (só UX) e devolve token fictício + last4/brand como HINTS.
 * Os dados crus são descartados aqui — nunca vão à rede, ao log ou ao localStorage.
 * Real (futuro): substituir o corpo por gateway.tokenize(...) / hosted fields (SAQ A).
 */
export async function tokenizeCard(card: RawCard): Promise<CardToken> {
  const number = card.number.replace(/\D/g, "");
  if (!luhnValid(number)) throw new Error("card_invalid");
  if (!validExp(card.exp)) throw new Error("exp_invalid");
  if (!/^\d{3,4}$/.test(card.cvv.trim())) throw new Error("cvv_invalid");
  if (!card.holder.trim()) throw new Error("holder_invalid");
  if (!isValidCpf(card.cpf)) throw new Error("cpf_invalid");
  const uuid =
    globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return { token: `tok_mock_${uuid}`, last4: number.slice(-4), brand: cardBrand(number) };
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `npx tsx --test src/services/payment/tokenize.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/payment/tokenize.ts src/services/payment/tokenize.test.ts
git commit -m "feat(front): tokenizeCard mock (Luhn/last4/brand, descarta dados crus)"
```

---

## Task 5: `CreditCardForm.tsx` — formulário dev-only

**Files:** Create `components/cases/payment/CreditCardForm.tsx`

- [ ] **Step 1:** Criar o componente (client). Props:
`{ onSubmit: (card: RawCard) => void; submitting: boolean; parcelasLabel: string }`.
Campos controlados: número (máscara `#### #### #### ####`), validade `MM/AA`, CVV, nome do titular, CPF
(reusar `maskCpf`). Validação de exibição chamando `luhnValid`/`validExp` do `tokenize.ts` (exportar
`validExp` se preciso) e `isValidCpf`. Botão desabilitado enquanto inválido ou `submitting`. Banner
"Pagamento simulado — ambiente de desenvolvimento". **Regras:** nenhum `console.*`/`JSON.stringify` do
estado; nenhum `localStorage`; `autocomplete="cc-number|cc-exp|cc-csc|cc-name"`. Ao submeter, chama
`onSubmit(card)` e **limpa os campos** do estado.

- [ ] **Step 2:** Run: `npm run lint && npm run typecheck`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add "components/cases/payment/CreditCardForm.tsx"
git commit -m "feat(front): CreditCardForm dev-only (validacao de UX, sem persistir)"
```

---

## Task 6: tela de pagamento + `cases.ts` (envia só o token)

**Files:** Modify `src/services/cases.ts`, `src/app/cases/[id]/pagamento/page.tsx`

- [ ] **Step 1:** Em `src/services/cases.ts`, `CasePaymentPayload` ganha os campos de cartão:

```ts
export type CasePaymentPayload = {
  parcelas: number;
  method: PaymentMethod;
  pricing_config_version?: number;
  idempotency_key: string;
  card_token?: string;
  card_last4?: string;
  card_brand?: string;
  card_holder?: string;
};
```

- [ ] **Step 2:** Na tela `pagamento/page.tsx`: quando `method==="cartao"` **e**
`estimate?.payment_mode==="mock"`, renderiza `<CreditCardForm>` e o botão "Confirmar" fica no form. No
submit: `const tok = await tokenizeCard(card)` → `createCasePayment(id, { parcelas, method:"cartao",
pricing_config_version, idempotency_key, card_token: tok.token, card_last4: tok.last4,
card_brand: tok.brand, card_holder: card.holder })`. Pix/Boleto seguem o botão atual. Erro de
tokenização/cobrança → mensagem **genérica** ("Não foi possível validar o cartão"), nunca o erro cru.

- [ ] **Step 3:** Run: `npm run lint && npm run typecheck`
Expected: exit 0.

- [ ] **Step 4:** Verificação manual: caso pago via cartão mostra bandeira + `••••1234`; o payload de rede
não contém número/CVV (checar na aba Network).

- [ ] **Step 5: Commit**

```bash
git add src/services/cases.ts "src/app/cases/[id]/pagamento/page.tsx"
git commit -m "feat(front): cartao na tela de pagamento (tokeniza e envia so o token)"
```

---

## Task 7: gate final + E2E de segurança

- [ ] **Step 1: Backend** — Run: `./.venv/Scripts/python.exe -m pytest -q`  → tudo verde.
- [ ] **Step 2: Frontend** — Run: `npm run lint && npm run typecheck && npm run test && npm run build` → exit 0.
- [ ] **Step 3: E2E de segurança (Playwright + captura de rede):** logar, criar caso, pagar com cartão
mock; capturar a requisição `POST /cases/{id}/payment` e **assertar** que o corpo **não** contém número
completo, CVV nem validade (só `card_token`/`card_last4`/`card_brand`); confirmar que o caso mostra
`••••last4` + bandeira; confirmar 0 erros de console e nenhum dado de cartão em `console`/`localStorage`.
- [ ] **Step 4:** (validação; sem commit novo)

---

## Nota de pré-produção (Fase 7)
Trocar o form próprio por **hosted fields/iframe** do gateway (SAQ A); `tokenizeCard` real via SDK;
webhook assinado; CSP + origem restrita; logs sanitizados. Ver §7 do spec.
