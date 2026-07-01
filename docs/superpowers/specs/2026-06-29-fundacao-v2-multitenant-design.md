# Fundação V2 — Multi-tenant + Tabelas Estruturais (design)

**Data:** 2026-06-29 · **Revisado:** 2026-06-30 (incorpora laudo de auditoria do Codex — ver §10)
**Projeto:** `contrato_visto_backend` (serverless AWS Lambda)
**Sub-projeto:** Fundação V2 (primeiro de: Fundação → SP2 ingestão → SP1 SQS/DLQ → SP4 adapters → SP3 agentes → SP5 billing)
**Referência canônica:** `X:\QUOARYA\legaltech-aws` (FastAPI) e `LEGALTECH-CONTRATO-VISTO-main` — modelos já testados que estamos **portando** para o serverless.

---

## 1. Contexto e objetivo

O backend serverless migrou o **núcleo** (users/clients/cases/case_results/documents/search) com isolamento **por-dono** (RLS `created_by/uploaded_by = app.user_id`). O produto-alvo V2 (legaltech-aws) é **multi-tenant** (`organization_id` em todas as entidades) e tem um modelo de domínio bem maior (request, partes, triagem, agentes, providers, cache externo, pricing).

**Objetivo desta fundação:** alinhar o serverless ao modelo V2 — adicionar `organization_id` + **RLS por organização** (com defesa em profundidade) e criar as tabelas estruturais que faltam — para que os sub-projetos SP1–SP5 nasçam já no modelo correto. **Escopo é só modelo + isolamento + auth**; nenhuma lógica de fila/OCR/agente/adapter entra aqui.

## 2. Decisões aprovadas (2026-06-29)

| # | Decisão | Escolha |
|---|---------|---------|
| Auth | Cognito vs JWT custom | **Manter JWT HS256 custom**; adicionar `organization_id` ao claim. Cognito fica para a fase AWS. |
| Isolamento | por-dono vs por-org | **Por-organização**. RLS via `organization_id`; `created_by`/`uploaded_by` viram só autoria. Isolamento de tenant absoluto. |
| Onboarding | org default vs própria vs pending | **Signup cria organização nova e o usuário vira `admin` dela**. Convite/aprovação fica para depois. |
| Legado | dropar vs deprecar | **Deprecar** `system.*` e `integration.*` órfãos; dropar em limpeza futura. |

A auditoria Codex (2026-06-30) acrescentou exigências de **defesa em profundidade** (FORCE RLS, views `security_invoker`, FKs compostas por tenant, `organization_id` em chunks/embeddings, conversão `users.id`→uuid). Incorporadas abaixo e rastreadas em §10.

## 3. Arquitetura

### 3.1 Isolamento (RLS por organização + defesa em profundidade)
- Toda tabela sensível ganha `organization_id uuid NOT NULL`; policy base:
  `organization_id = current_setting('app.organization_id')::uuid` (USING + WITH CHECK), **fail-closed** (sem o setting, `current_setting` estoura e nega tudo).
- **`FORCE ROW LEVEL SECURITY`** em todas as tabelas sensíveis (cobre owner/funções/views, não só o `cv_app` não-owner). [BLOQ-2/melhoria FORCE-RLS]
- **Integridade cross-tenant:** cada tabela "pai" ganha `UNIQUE(organization_id, id)`; as FKs filhas passam a ser **compostas** `(organization_id, parent_id) → (organization_id, id)`. Impede que uma linha da org A referencie pai da org B. [BLOQ-3]
- **`document_chunks`/`document_embeddings` ganham `organization_id` próprio + RLS + FK composta para `documents`** (não confiar só no JOIN). [BLOQ-4]
- **Views:** `cases_with_latest_result` e `documents_with_embeddings` recriadas `WITH (security_invoker = true)` (senão executam como owner e furam RLS). [BLOQ-2]

### 3.2 Auth (propagação do tenant)
- `create_access_token` inclui `organization_id` no payload do JWT.
- **`jwt_authorizer.authorize` exige e valida `organization_id` (UUID) no claim e o repassa no `context`; nega token sem org (não delegar essa validação só ao `context.py`).** [melhoria authorizer]
- `context.py::get_user_from_event` também valida `organization_id`.
- `database.py::tenant_tx(user_id, role, organization_id)` seta `app.user_id`, `app.user_role` **e** `app.organization_id` (todos via `set_config(..., true)` = SET LOCAL, seguro com RDS Proxy transaction mode).

### 3.3 Onboarding (signup atômico, compatível com RLS)
`POST /users` (signup) numa **única transação**:
1. gera `org_id = uuid` no app;
2. `set_config('app.organization_id', org_id, true)` e `set_config('app.user_role','admin',true)`;
3. `INSERT organizations(id = org_id, ...)` → passa no `WITH CHECK` porque `id = app.organization_id`;
4. `INSERT users(... , organization_id = org_id, role='admin')` (users sem RLS — ver §5).
Sem org default compartilhada; `org_id` nunca vem do cliente. [BLOQ-1]
`login` emite JWT com o `organization_id` do usuário.

## 4. Modelo de dados

### 4.1 Alterações em tabelas existentes
- **`users`**: `+ organization_id uuid NOT NULL`. **Converter `users.id` de `varchar(36)` para `uuid`** (e `password_resets.user_id` idem) para permitir FKs corretas a partir das tabelas novas. [melhoria users.id]
- **`clients`**: `+ organization_id uuid NOT NULL`; trocar `UNIQUE(document_number)` por **`UNIQUE(organization_id, document_number)`**; `+ UNIQUE(organization_id, id)`; RLS por org + FORCE RLS.
- **`cases`**: `+ organization_id uuid NOT NULL`, `+ UNIQUE(organization_id, id)`, FK composta `(organization_id, client_id) → clients`; `client_id` passa a **nullable**; `+ request_id`, `+ product_type`, `+ product_label`, `+ risk_level default 'unknown'`, `+ recommendation`, `+ progress default 0`, `+ source_mode default 'local'`, `+ code`, `+ title`, `+ description`, `+ submitted_at`, `+ deleted_at` (novas colunas nullable/default p/ não quebrar CRUD).
- **`documents`**: `+ organization_id uuid NOT NULL`, `+ UNIQUE(organization_id, id)`, FK composta `(organization_id, case_id) → cases`; `+ status default 'pending_upload'`, `+ conversion_status default 'pending'`, `+ normalized_markdown_storage_key/sha256/size`, `+ conversion_error_summary`, `+ converted_at`, `+ storage_bucket`, `+ storage_key`.
- **`case_results`**: `+ organization_id uuid NOT NULL`, FK composta `(organization_id, case_id) → cases`.
- **`document_chunks` / `document_embeddings`**: `+ organization_id uuid NOT NULL` + RLS própria + FK composta para `documents`. [BLOQ-4]
- **`audit.audit_log`** e a função `audit.log_audit()`: gravar `organization_id` (de `current_setting('app.organization_id')`) para rastreabilidade multi-tenant. [melhoria auditoria]
- **Views** `cases_with_latest_result`, `documents_with_embeddings`: recriar `WITH (security_invoker = true)`. [BLOQ-2]

### 4.2 Tabelas novas (em `public`; `organization_id NOT NULL` + RLS + FORCE RLS; `id uuid default gen_random_uuid()`; `created_at/updated_at timestamptz`; `UNIQUE(organization_id, id)`; FKs compostas por tenant; FKs para `users(id)` agora válidas após `users.id→uuid`)

| Tabela | Colunas-chave / observações |
|--------|------------------------------|
| `organizations` | `name`, `document`, `status default active`, `metadata jsonb`, `deleted_at`. RLS: `id = app.organization_id`. |
| `request_code_sequences` | `(organization_id, year)` **UNIQUE** (não `year` PK), `next_number`. [melhoria] |
| `requests` | `created_by`, `code`, `product_type`, `product_label`, `title`, `description`, `status`, `source_mode`, `idempotency_key`, `case_id`, `total_price_cents`, `price_snapshot jsonb` |
| `case_parties` | `case_id`, `party_type`, `name`, `document`, `metadata jsonb`, `deleted_at` |
| `triage_modules` | `case_id`, `module_key`, `module_label`, `provider`, `status`, `source_mode`, `required bool`, `reason`, `started_at`, `finished_at`, `attempts int`, `error_code`, `error_message`, `summary`, `result_ref`, `raw_result_ref` |
| `provider_results` | `case_id`, `triage_module_id`, `provider`, `provider_request_id`, `source_mode`, `status`, `input_hash`, `raw_result_ref`, `normalized_result jsonb`, `summary`, `risk_signals jsonb '[]'`, `confidence float`, `error_*` |
| `agent_executions` | `case_id`, `document_id`, `job_id uuid`, **UNIQUE(`organization_id`,`job_id`)**, `agent_type`, `status default queued`, `attempt int default 1`, `input_payload`, `output_payload`, `error_message`, `started_at`, `completed_at` [melhoria job_id] |
| `external_queries_cache` | `case_id`, `provider`, `query_hash`, `request_payload`, `response_payload`, `normalized_payload`, `status default pending`, `error_message`, `requested_by`. **UNIQUE(`organization_id`,`provider`,`query_hash`)** |
| `pricing_configs` | **UNIQUE `organization_id`**, `cases_limit int`, `product_overrides jsonb '{}'`, `module_overrides jsonb '{}'`, `version int default 1`, `notes`, `updated_by` |

Índices por tenant: `idx_<t>_organization_id` e compostos `org+status`/`org+case`.

### 4.3 Catálogo de pricing (código, no SP5)
Portar `pricing/config.py` (4 produtos × 7 módulos + matriz). Aqui só a **tabela** `pricing_configs`.

## 5. RLS — políticas
- **Tabelas org-scoped** (clients, cases, case_results, documents, document_chunks, document_embeddings, e todas as novas exceto organizations): por operação (SELECT/INSERT/UPDATE/DELETE)
  `USING/WITH CHECK (organization_id = current_setting('app.organization_id')::uuid)` + `FORCE ROW LEVEL SECURITY`.
- **`organizations`**: `USING/WITH CHECK (id = current_setting('app.organization_id')::uuid)` + FORCE RLS. (Signup seta o contexto antes do INSERT — §3.3.)
- **`users` e `password_resets`**: **sem RLS** (login/signup precisam ler por email antes de haver contexto). O isolamento é na **aplicação**: todo `SELECT/UPDATE/DELETE` de users filtra `organization_id = user["organization_id"]`; **anti-lockout do último admin é por organização**. [BLOQ-5]
- Trigger de auditoria permanece `SECURITY DEFINER` (append-only), agora gravando `organization_id`.

## 6. Decomposição em migrações (cada uma: precheck → DDL → backfill → VALIDATE → testes PG18; rollback documentado) [BLOQ-6]
1. **005** — `organizations` (+RLS/FORCE/índice) + seed de org de sistema (para backfill).
2. **006** — `users.id varchar→uuid` (+ `password_resets.user_id`), `users.organization_id`; auth (`create_access_token`/`jwt_authorizer`/`context.py`/`tenant_tx` propagam e validam org); signup atômico cria org+admin. Precheck: todos os `users.id` são UUID válidos.
3. **007** — `organization_id` + RLS/FORCE + `UNIQUE(org,id)` + FKs compostas em `clients`, `cases`, `case_results`, `documents`; `clients` UNIQUE por org; `cases.client_id` nullable; views `security_invoker`; backfill p/ org de sistema. Usar `ADD CONSTRAINT ... NOT VALID` + `VALIDATE CONSTRAINT`.
4. **008** — `document_chunks`/`document_embeddings` `organization_id` + RLS + FK composta; `audit_log.organization_id` + função.
5. **009** — `requests` + `request_code_sequences` + `case_parties` (+ colunas estruturais de `cases`).
6. **010** — `triage_modules` + `provider_results` + `agent_executions` + `external_queries_cache`.
7. **011** — `pricing_configs`.

(Limpeza das tabelas legadas `system.*`/`integration.*` fica para migração de limpeza posterior.)

## 7. Testes (pytest no PG18, role `cv_app` não-owner)
- **Isolamento:** org A não vê dados da org B (SELECT vazio); INSERT cross-org barrado por `WITH CHECK`.
- **Via VIEW:** consulta às views como org A não retorna linhas de B (regressão do BLOQ-2).
- **Cross-tenant FK:** inserir filho com `(org, parent_id)` de outra org falha (BLOQ-3).
- **chunks/embeddings:** query direta como org A não lê chunks de B (BLOQ-4).
- **Fail-closed:** sem `app.organization_id`, toda query nega.
- **Auth:** authorizer rejeita JWT sem `organization_id`; `tenant_tx` seta os 3 settings.
- **Onboarding:** signup cria org + admin atômico; login emite JWT com a org; signup concorrente não cruza orgs.
- **users por org:** `list_users`/`get`/`update`/`delete` só enxergam a própria org; anti-lockout por org (BLOQ-5).
- **Não-regressão:** os 141 testes passam após adaptação das fixtures (injetar `organization_id`).
- **Novas tabelas:** CRUD com RLS; `agent_executions` UNIQUE(org,job_id); cache UNIQUE(org,provider,hash); `pricing_configs` UNIQUE(org).

## 8. Riscos e mitigação
- **Conversão `users.id`→uuid** (PK referenciada) → precheck de formato + recriar FKs; rodar em transação; backup do schema dev.
- **Quebra dos 141 testes** → adaptar fixtures p/ `organization_id`; suíte verde a cada migração.
- **Views furando RLS** → `security_invoker=true` + teste de regressão explícito.
- **Backfill** → org de sistema (ambiente dev; produção ainda não existe).
- **Escopo grande** → migração a migração, TDD, verde antes de seguir.

## 9. Fora de escopo (próximos sub-projetos)
Cognito (fase AWS); SQS/DLQ/workers (SP1); OCR/markdown/chunking/embeddings (SP2); adapters externos + evidência (SP4); lógica dos agentes + revisão humana (SP3); serviço de pricing/snapshot (SP5). Aqui entregamos só o modelo multi-tenant + tabelas/colunas que esses SPs preencherão.

## 10. Achados da auditoria Codex (2026-06-30) e onde foram tratados
| Achado | Severidade | Tratado em |
|--------|-----------|-----------|
| 1. `organizations`/onboarding sob RLS | BLOQUEANTE | §3.3, §5 (signup atômico; policy `id=app.org_id`) |
| 2. Views furam RLS (owner) | BLOQUEANTE | §3.1, §4.1, §5 (`security_invoker=true` + FORCE RLS) + teste §7 |
| 3. Integridade cross-tenant (FK só por id) | BLOQUEANTE | §3.1, §4.1/4.2 (`UNIQUE(org,id)` + FKs compostas) + teste §7 |
| 4. chunks/embeddings sem org_id | BLOQUEANTE | §3.1, §4.1 (org_id + RLS + FK composta) + teste §7 |
| 5. users sem RLS → filtro por org nos handlers | BLOQUEANTE | §5 (filtro app + anti-lockout por org) + teste §7 |
| 6. Migrações sem precheck/rollback | BLOQUEANTE | §6 (precheck→DDL→backfill→VALIDATE; NOT VALID/VALIDATE) |
| authorizer validar org_id | melhoria | §3.2 |
| `request_code_sequences` UNIQUE(org,year) | melhoria | §4.2 |
| `agent_executions` UNIQUE(org,job_id) | melhoria | §4.2 |
| `users.id` varchar→uuid + FKs | melhoria | §4.1, §6 (migr. 006), §8 |
| auditoria com org_id | melhoria | §4.1 (migr. 008) |
| FORCE RLS (hardening) | melhoria | §3.1, §5 |
