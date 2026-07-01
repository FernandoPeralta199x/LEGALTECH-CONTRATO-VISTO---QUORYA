# FE-4 — Auditoria de divergências de contrato (Frontend ↔ Backend serverless)

> Análise **ponta a ponta** das chamadas de API do frontend (`contrato_visto_frontend/src/services/*.ts`)
> contra o backend serverless (`serverless.yml` + `src/handlers/*.py`). **Fase de ANÁLISE** —
> nada corrigido aqui além do que já foi feito nesta sessão (✅). Consolidado a partir de duas
> auditorias independentes (esta sessão + Codex).
>
> Roteamento: o frontend chama `/api/v1/<x>`; o dev-server normaliza para `/<x>`. O `apiClient`
> tolera o envelope `{data,message}` no sucesso; em erro mostra mensagem genérica de status.

**Severidade:** 🔴 BLOQUEANTE (quebra a tela) · 🟡 MÉDIA (degrada/edição) · ⚪ BAIXA (futuro/cosmético)

---

## Já corrigido nesta sessão ✅
- `POST /auth/login` — shape `{access_token, token_type, expires_in, user}` + claims `sub`/`token_use:"dev"`.
- `GET /me` + `GET /auth/me` — alias criado.
- `GET /documents` — lista criada, mapeando schema legado → V2.

---

## Tabela consolidada de divergências

| # | Serviço | Frontend | Backend | Divergência | Sev. | Correção (onde) |
|---|---|---|---|---|---|---|
| 1 | auth | `POST /auth/login` | ✅ | — | ✅ | — |
| 2 | auth | `GET /me` | ✅ | — | ✅ | — |
| 3 | auth | `POST /auth/register` | ❌ (só `POST /users`) | rota/shape | 🟡 | FRONTEND usar `/users` (ou BACKEND criar alias) |
| 4 | auth | `POST /auth/verify-email` | ❌ | rota ausente | 🟡 | FRONTEND remover etapa (modelo sem verificação) |
| 5 | **cases** | `GET /cases` | ✅ (array) | `mapBackendCase` lê `client_id.slice()` (null) + `metadata` (ausente) → **quebra** | 🔴 | BACKEND: retornar **página** `{items,…}` operacional (→ `mapOperationalCase`, tolerante) OU FRONTEND null-safety |
| 6 | **clients** | `GET /clients` | ✅ | `_serialize` retorna `legal_name/document_number`; frontend espera `name/document/metadata/updated_at` e faz `displayName.slice()` → **quebra** | 🔴 | BACKEND: aliases `name/document/metadata/updated_at` em `clients._serialize` |
| 7 | documents | `GET /documents` | ✅ criado | filtro `status` não aplicado | ⚪ | BACKEND: aplicar filtro `status` |
| 8 | **cases** | `GET /cases/{id}` | ✅ | `_serialize` sem `metadata/submitted_at/updated_at`; `client_id` null | 🔴 | BACKEND: alinhar `_serialize` (já tem campos de produto; faltam metadata/updated_at) |
| 9 | **cases** | `GET /cases/{id}/aggregate` | ❌ | rota ausente (só `/parties`,`/timeline`,`/triage`) | 🔴 | BACKEND: criar `aggregate` (`{case,request,parties,documents,timeline,triage_modules,provider_results,report,summary}`) |
| 10 | cases | `POST /requests` (wizard) | ✅ | faltam `case_status/documents_count/timeline_events_count` + campos `BackendLegalRequest` | 🟡 | BACKEND: enriquecer resposta |
| 11 | cases | `POST /cases` | ✅ | `CaseCreate(extra='forbid')` rejeita `metadata`; retorno mínimo | 🟡 | FE+BE: alinhar payload + serializar `BackendCase` |
| 12 | cases | `PATCH /cases/{id}` | ⚠️ só `PUT` | método + sem `data` no retorno | 🟡 | BACKEND: aceitar `PATCH` + retornar caso serializado |
| 13 | cases | `DELETE /cases/{id}` | ✅ (hard) | UI promete soft-delete; handler faz hard | 🟡 | BACKEND: soft-delete (`deleted_at`) ou FE ajustar texto |
| 14 | clients | `POST /clients` | ✅ | `ClientCreateSchema(extra='forbid')` + retorno mínimo | 🟡 | FE+BE: alinhar payload + serializar |
| 15 | clients | `GET /clients/{id}` | ✅ | mesmo shape do #6 | 🟡 | BACKEND: alinhar `_serialize` |
| 16 | clients | `PATCH /clients/{id}` | ⚠️ só `PUT` | método + sem `data` | 🟡 | BACKEND: aceitar `PATCH` + retornar cliente serializado |
| 17 | caseParties | `GET /cases/{id}/parties` | ✅ | retorna `*_masked`; frontend espera `document/email/phone` | 🟡 | FRONTEND: mapear `*_masked` (manter PII mascarada) |
| 18 | caseParties | `POST /cases/{id}/parties` | ❌ | rota ausente | 🟡 | BACKEND: criar (com timeline `party_added`) |
| 19 | caseParties | `PATCH /cases/{id}/parties/{pid}` | ❌ | rota ausente | 🟡 | BACKEND: criar update |
| 20 | documents | `POST /documents` | ✅ (presign) | retorna `upload_url`; frontend espera `BackendDocument` | 🟡 | FE: tratar como presign; ou BE retornar doc |
| 21 | documents | `POST /documents/upload` (multipart) | ❌ | serverless usa JSON+presign, não multipart | 🟡 | FRONTEND: POST `/documents` JSON + PUT no `upload_url` |
| 22 | documents | `GET /documents/{id}` | ✅ | usa shape legado (não `_serialize_v2`) | 🟡 | BACKEND: usar `_serialize_v2` também aqui |
| 23 | documents | `PATCH /documents/{id}` | ❌ | rota ausente | ⚪ | BACKEND ou FE remover |
| 24 | documents | `GET /documents/{id}/download-url` | ❌ | rota ausente | 🟡 | BACKEND: criar `{url,expires_in_seconds,method}` |
| 25 | documents | `POST /documents/{id}/enqueue-processing` | ❌ | rota ausente (SP2) | ⚪ | BACKEND (SP2) ou FE desabilitar |
| 26 | finalReports | `GET /cases/{id}/documents` | ❌ | rota ausente | 🟡 | FRONTEND: usar `/documents?case_id={id}` |
| 27 | finalReports | upload/download | ❌ | mesmas dos docs | 🟡 | FRONTEND: reusar presign |
| 28 | pricing | `GET /pricing` · `POST /estimate` · `GET/PUT /config` · `GET /config/limit-check` | ✅ | alinhado | ✅ | — |
| 29 | pricing | `GET /pricing/config` | ✅ | `updated_at` pode ser `null`; tipo diz `string` | ⚪ | FRONTEND: tipar `string \| null` |

---

## 🔴 BLOQUEANTES do caminho de LEITURA (login → dashboard → casos → detalhe → pricing)

Estas 4 quebram o fluxo que o usuário percorre primeiro. **Todas no BACKEND.**

1. **`GET /cases` (lista)** — #5. Backend retorna array → frontend quebra (`client_id` null + `metadata`).
   → `list_cases` retornar **página operacional** `{items, page, page_size, total, total_pages}`.
2. **`GET /clients` (lista)** — #6. Shape `legal_name/document_number` ≠ `name/document` esperado (+ `displayName.slice`).
   → aliases `name/display_name/document/metadata/updated_at` em `clients._serialize`.
3. **`GET /cases/{id}`** — #8. Faltam `metadata/submitted_at/updated_at`; `client_id` null.
   → alinhar `cases._serialize`.
4. **`GET /cases/{id}/aggregate`** — #9. Endpoint agregado do detalhe não existe.
   → criar handler `aggregate` (reusa SELECTs já existentes).

> Pricing e wizard (`POST /requests`) já respondem; o wizard só precisa de enriquecimento (#10, MÉDIA).

---

## 🟡 MÉDIAS — destravam ESCRITA/edição (fase 2)
- `PATCH` em `cases/{id}` e `clients/{id}` (#12, #16) + retornar entidade serializada.
- `POST`/serialização de `cases` e `clients` (#11, #14) — aceitar payload e devolver shape completo.
- `POST`/`PATCH` de partes (#18, #19).
- Enriquecer resposta do wizard (#10).
- `download-url` de documento (#24); `get_document` usar `_serialize_v2` (#22).
- Mapear `*_masked` no frontend de partes (#17).
- Cadastro: `/auth/register` + `/auth/verify-email` (#3, #4).

## ⚪ BAIXA / fases SP2-SP3
- Upload multipart → presign (#21, #27), `enqueue-processing` (#25), `PATCH /documents` (#23),
  `finalReports` (#26), filtros e tipagens cosméticas (#7, #29), soft-delete (#13).

---

## Plano de correção (após OK do usuário)
**Fase 1 — desbloquear leitura (backend):** #5 (página) → #6 (clients serialize) → #8 (case serialize) → #9 (aggregate). Reteste E2E no browser (dashboard → casos → detalhe) + pytest.
**Fase 2 — escrita/edição:** #12/#16 (PATCH) → #11/#14 (POST shapes) → #18/#19 (parties) → #10 (wizard).
**Fase 3 — documentos/relatórios (SP2):** #21/#22/#24/#26/#27 + ingestão.
