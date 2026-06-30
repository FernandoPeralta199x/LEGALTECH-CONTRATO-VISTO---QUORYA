# Fundação V2 — Parte 1B: RLS por Organização (migração 007) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps usam checkbox (`- [ ]`).

**Goal:** Virar o isolamento de **por-dono → por-organização** nas 4 tabelas com handlers (`clients`, `cases`, `case_results`, `documents`): adicionar `organization_id`, reescrever as policies RLS para `organization_id = app.organization_id` (+ FORCE RLS), defesa em profundidade (UNIQUE(org,id) + FKs compostas por tenant), `clients` UNIQUE por org, e views com `security_invoker`. Handlers passam a inserir `organization_id`; os testes de isolamento são reescritos para a semântica de tenant.

**Architecture:** Migração `007` (idempotente, transação única) altera as 4 tabelas. Como o app conecta como `cv_app` (não-owner) e o contexto `app.organization_id` já é setado por `tenant_tx` (Parte 1A), os handlers só precisam **incluir `organization_id` no INSERT** (= `user["organization_id"]`, que casa com o `WITH CHECK`). `document_chunks`/`document_embeddings` + `rag` + auditoria ficam para a **Parte 1C** (continuam protegidos por JOIN com `documents` nesta fase).

**Tech Stack:** PostgreSQL 18, psycopg2, pytest; migrações `.sql` aplicadas via `scratchpad/apply_migration.py` (owner `dbadmin`); app = `cv_app`. Org de sistema: `00000000-0000-0000-0000-000000000001` (`SYSTEM_ORG_ID`).

**Estado real (diagnosticado):** clients (RLS off, UNIQUE(document_number) global, 1 linha), cases/case_results/documents (RLS on por-dono, sem FORCE), nenhuma tem `organization_id`. cases.client_id é NOT NULL.

---

## File Structure
- Create: `migrations/007_rls_por_organizacao.sql`
- Modify: `src/handlers/cases.py` (INSERT cases + organization_id)
- Modify: `src/handlers/clients.py` (INSERT clients + organization_id)
- Modify: `src/handlers/case_results.py` (INSERT…SELECT + organization_id)
- Modify: `src/handlers/documents.py` (INSERT…SELECT + organization_id)
- Modify: `tests/test_rls.py`, `tests/test_cases_handlers.py`, `tests/test_clients_handlers.py`, `tests/test_documents_handlers.py` (semântica de isolamento por-org)

---

## Task 1: Migração 007 (schema + RLS por org)

**Files:** Create `migrations/007_rls_por_organizacao.sql`

- [ ] **Step 1: Escrever a migração**

```sql
-- Migration 007 — isolamento por ORGANIZAÇÃO (substitui por-dono) em
-- clients, cases, case_results, documents. Defesa em profundidade: FORCE RLS,
-- UNIQUE(org,id), FKs compostas por tenant, views security_invoker.
BEGIN;

-- 1) organization_id + backfill p/ org de sistema + NOT NULL + FK + índice
ALTER TABLE public.clients      ADD COLUMN IF NOT EXISTS organization_id uuid;
ALTER TABLE public.cases        ADD COLUMN IF NOT EXISTS organization_id uuid;
ALTER TABLE public.case_results ADD COLUMN IF NOT EXISTS organization_id uuid;
ALTER TABLE public.documents    ADD COLUMN IF NOT EXISTS organization_id uuid;

UPDATE public.clients      SET organization_id = '00000000-0000-0000-0000-000000000001' WHERE organization_id IS NULL;
UPDATE public.cases        SET organization_id = '00000000-0000-0000-0000-000000000001' WHERE organization_id IS NULL;
UPDATE public.case_results SET organization_id = '00000000-0000-0000-0000-000000000001' WHERE organization_id IS NULL;
UPDATE public.documents    SET organization_id = '00000000-0000-0000-0000-000000000001' WHERE organization_id IS NULL;

ALTER TABLE public.clients      ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE public.cases        ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE public.case_results ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE public.documents    ALTER COLUMN organization_id SET NOT NULL;

ALTER TABLE public.clients      ADD CONSTRAINT clients_org_fkey      FOREIGN KEY (organization_id) REFERENCES public.organizations(id);
ALTER TABLE public.cases        ADD CONSTRAINT cases_org_fkey        FOREIGN KEY (organization_id) REFERENCES public.organizations(id);
ALTER TABLE public.case_results ADD CONSTRAINT case_results_org_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);
ALTER TABLE public.documents    ADD CONSTRAINT documents_org_fkey    FOREIGN KEY (organization_id) REFERENCES public.organizations(id);

CREATE INDEX IF NOT EXISTS idx_clients_org      ON public.clients(organization_id);
CREATE INDEX IF NOT EXISTS idx_cases_org        ON public.cases(organization_id);
CREATE INDEX IF NOT EXISTS idx_case_results_org ON public.case_results(organization_id);
CREATE INDEX IF NOT EXISTS idx_documents_org    ON public.documents(organization_id);

-- 2) integridade cross-tenant: UNIQUE(org,id) nos pais + FKs compostas
ALTER TABLE public.clients ADD CONSTRAINT clients_org_id_uniq UNIQUE (organization_id, id);
ALTER TABLE public.cases   ADD CONSTRAINT cases_org_id_uniq   UNIQUE (organization_id, id);

ALTER TABLE public.cases        ALTER COLUMN client_id DROP NOT NULL;  -- V2: request pode preceder o client
ALTER TABLE public.cases        DROP CONSTRAINT IF EXISTS cases_client_id_fkey;
ALTER TABLE public.cases        ADD  CONSTRAINT cases_client_fkey FOREIGN KEY (organization_id, client_id) REFERENCES public.clients(organization_id, id) ON DELETE CASCADE;
ALTER TABLE public.case_results DROP CONSTRAINT IF EXISTS case_results_case_id_fkey;
ALTER TABLE public.case_results ADD  CONSTRAINT case_results_case_fkey FOREIGN KEY (organization_id, case_id) REFERENCES public.cases(organization_id, id) ON DELETE CASCADE;
ALTER TABLE public.documents    DROP CONSTRAINT IF EXISTS documents_case_id_fkey;
ALTER TABLE public.documents    ADD  CONSTRAINT documents_case_fkey FOREIGN KEY (organization_id, case_id) REFERENCES public.cases(organization_id, id) ON DELETE CASCADE;

-- 3) clients: UNIQUE por org (substitui global)
ALTER TABLE public.clients DROP CONSTRAINT IF EXISTS clients_document_number_key;
ALTER TABLE public.clients ADD  CONSTRAINT clients_org_document_uniq UNIQUE (organization_id, document_number);

-- 4) policies por ORG (substituem por-dono) + FORCE RLS
-- cases
DROP POLICY IF EXISTS case_owner_access ON public.cases;
DROP POLICY IF EXISTS case_insert ON public.cases;
DROP POLICY IF EXISTS case_update ON public.cases;
DROP POLICY IF EXISTS case_delete ON public.cases;
CREATE POLICY cases_org_all ON public.cases FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
ALTER TABLE public.cases FORCE ROW LEVEL SECURITY;
-- case_results
DROP POLICY IF EXISTS case_results_select ON public.case_results;
DROP POLICY IF EXISTS case_results_insert ON public.case_results;
DROP POLICY IF EXISTS case_results_update ON public.case_results;
DROP POLICY IF EXISTS case_results_delete ON public.case_results;
CREATE POLICY case_results_org_all ON public.case_results FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
ALTER TABLE public.case_results FORCE ROW LEVEL SECURITY;
-- documents
DROP POLICY IF EXISTS documents_select ON public.documents;
DROP POLICY IF EXISTS documents_insert ON public.documents;
DROP POLICY IF EXISTS documents_update ON public.documents;
DROP POLICY IF EXISTS documents_delete ON public.documents;
CREATE POLICY documents_org_all ON public.documents FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
ALTER TABLE public.documents FORCE ROW LEVEL SECURITY;
-- clients (estava sem RLS): catálogo agora é por org
ALTER TABLE public.clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clients FORCE ROW LEVEL SECURITY;
CREATE POLICY clients_org_all ON public.clients FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);

-- 5) views com security_invoker (senão executam como owner e furam RLS)
DROP VIEW IF EXISTS public.cases_with_latest_result;
CREATE VIEW public.cases_with_latest_result WITH (security_invoker = true) AS
  SELECT c.id, c.client_id, c.case_type, c.status, c.created_at,
         cr.id AS latest_result_id, cr.result_type, cr.risk_level,
         cr.created_at AS result_created_at,
         row_number() OVER (PARTITION BY c.id ORDER BY cr.created_at DESC) AS result_rank
    FROM public.cases c LEFT JOIN public.case_results cr ON c.id = cr.case_id;
DROP VIEW IF EXISTS public.documents_with_embeddings;
CREATE VIEW public.documents_with_embeddings WITH (security_invoker = true) AS
  SELECT d.id, d.case_id, d.file_name, d.document_classification,
         count(de.id) AS embedding_count, max(de.created_at) AS last_embedding_date
    FROM public.documents d LEFT JOIN public.document_embeddings de ON d.id = de.document_id
   GROUP BY d.id, d.case_id, d.file_name, d.document_classification;

COMMIT;
```

- [ ] **Step 2: Aplicar** — `python scratchpad/apply_migration.py migrations/007_rls_por_organizacao.sql` → sem erro.
- [ ] **Step 3: Verificar** — re-rodar `scratchpad/inspect_007.py`: as 4 tabelas com `org_col=True`, `rls=True`, `forced=True`, policy `*_org_all`, FKs compostas.
- [ ] **Step 4: Commit** — `git add migrations/007_rls_por_organizacao.sql && git commit -m "feat(fundacao-v2): migracao 007 RLS por organizacao + defesa em profundidade"`

## Task 2: Handlers create_* inserem `organization_id`

**Files:** `src/handlers/cases.py`, `clients.py`, `case_results.py`, `documents.py`

- [ ] **Step 1:** `cases.create_case`: trocar `INSERT INTO public.cases (client_id, case_type, priority, created_by, metadata) VALUES (%s,%s,%s,%s,%s)` por incluir `organization_id` como 1ª coluna/valor (`user["organization_id"]`).
- [ ] **Step 2:** `clients.create_client`: incluir `organization_id` na lista de colunas e valores (`user["organization_id"]`).
- [ ] **Step 3:** `case_results.create_case_result` e `documents.upload_document` (INSERT…SELECT): incluir `organization_id` nas colunas e na lista do SELECT (`user["organization_id"]`). Manter o `WHERE EXISTS` do case visível.
- [ ] **Step 4:** Ler cada trecho real antes de editar (os INSERTs de case_results/documents são `INSERT…SELECT` — copiar a forma exata). Sem rodar ainda (testes na Task 3).

## Task 3: Reescrever testes de isolamento para semântica por-org + suíte verde

**Files:** `tests/test_rls.py`, `tests/test_cases_handlers.py`, `tests/test_clients_handlers.py`, `tests/test_documents_handlers.py`

- [ ] **Step 1:** `test_rls.py`: o isolamento agora é por **org**. `_seed_case(user, org)` recebe org; `test_owner_sees_case_other_user_does_not` → renomear para **`test_org_isolation`**: user da org A cria; outro user da **org B** não vê; outro user da **mesma org A** vê. `test_admin_sees_all_cases` → admin só vê da própria org. Ajustar `_reset` para também limpar nada de organizations além da de sistema.
- [ ] **Step 2:** `test_cases_handlers.py`/`test_documents_handlers.py`: `test_*_isolation_and_admin` passa a usar **orgs diferentes** para provar isolamento (mesmo dono não importa mais; org importa). O helper `_event(..., org_id=...)` já aceita org; usar org distinta para o "outro" usuário e a mesma org para o "admin" que deve enxergar.
- [ ] **Step 3:** `test_clients_handlers.py`: como clients agora tem RLS por org, o `_event` precisa de org consistente; o seed de client (se via admin_conn) precisa setar `app.organization_id` (FORCE RLS atinge o owner) — usar a org de sistema e inserir `organization_id`.
- [ ] **Step 4:** Rodar `pytest tests/ -q` → tudo verde.
- [ ] **Step 5:** Commit — `git add src/handlers tests && git commit -m "feat(fundacao-v2): isolamento por organizacao nos handlers + testes"`

## Riscos
- **FORCE RLS atinge setups via owner:** fixtures que inserem via `_admin_conn` em tabelas agora com FORCE RLS precisam `SET app.organization_id` antes (ou inserir já com a coluna correta e o GUC setado). Tratar em test_clients/test_rls.
- **FK composta exige UNIQUE(org,id) no pai:** criado para clients e cases (documents não é pai nesta parte).
- **Suíte fica vermelha entre Task 1 e Task 3** (esperado): só volta verde ao fim da Task 3.

## Fora de escopo (Parte 1C)
`document_chunks`/`document_embeddings` org + RLS + FK composta; `rag.store_chunk/embedding` recebem org; `_seed_embeddings`/ingestão setam contexto; `audit.audit_log.organization_id` + função.
