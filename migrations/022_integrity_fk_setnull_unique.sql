-- Migration 022 — Integridade referencial e unicidade (achados DB-01, DB-02, DB-03)
--
-- DB-01: os índices únicos PARCIAIS de fiscal_documents têm coluna-companheira
--   NULLABLE. Em UNIQUE, NULLs são DISTINTOS por padrão → duas notas reais com o
--   mesmo (numero) e serie NULL, ou o mesmo (external_id) e provider NULL, NÃO
--   colidem: a proteção contra NF duplicada fica furada quando a companheira é NULL.
--   Fix: NULLS NOT DISTINCT (PG15+; o ambiente é PG18) — trata NULLs como iguais.
--
-- DB-02/DB-03: as FKs COMPOSTAS org-scoped usam ON DELETE SET NULL SEM lista de
--   colunas. Nesse caso o Postgres zera TODAS as colunas da FK, inclusive
--   organization_id (NOT NULL) → a ação referencial viola not-null e ABORTA o DELETE
--   do pai (request/case/client). Fix: SET NULL (coluna) — PG15+ — zera só o vínculo
--   (request_id/case_id/client_id) e preserva organization_id.
--
-- Idempotente (DROP ... IF EXISTS antes de cada recriação). Rollback ao final.
BEGIN;

-- ── DB-01: unicidade de notas fiscais robusta a NULL na companheira ──────────────
DROP INDEX IF EXISTS public.uq_fiscal_documents_org_numero;
CREATE UNIQUE INDEX uq_fiscal_documents_org_numero
    ON public.fiscal_documents (organization_id, doc_type, serie, numero)
    NULLS NOT DISTINCT
    WHERE numero IS NOT NULL;

DROP INDEX IF EXISTS public.uq_fiscal_documents_org_extid;
CREATE UNIQUE INDEX uq_fiscal_documents_org_extid
    ON public.fiscal_documents (organization_id, provider, external_id)
    NULLS NOT DISTINCT
    WHERE external_id IS NOT NULL;

-- ── DB-02/03: SET NULL por-coluna (preserva organization_id NOT NULL) ────────────
-- external_api_costs (018)
ALTER TABLE public.external_api_costs DROP CONSTRAINT IF EXISTS external_api_costs_request_fkey;
ALTER TABLE public.external_api_costs ADD CONSTRAINT external_api_costs_request_fkey
    FOREIGN KEY (organization_id, request_id)
    REFERENCES public.requests(organization_id, id) ON DELETE SET NULL (request_id);
ALTER TABLE public.external_api_costs DROP CONSTRAINT IF EXISTS external_api_costs_case_fkey;
ALTER TABLE public.external_api_costs ADD CONSTRAINT external_api_costs_case_fkey
    FOREIGN KEY (organization_id, case_id)
    REFERENCES public.cases(organization_id, id) ON DELETE SET NULL (case_id);
ALTER TABLE public.external_api_costs DROP CONSTRAINT IF EXISTS external_api_costs_client_fkey;
ALTER TABLE public.external_api_costs ADD CONSTRAINT external_api_costs_client_fkey
    FOREIGN KEY (organization_id, client_id)
    REFERENCES public.clients(organization_id, id) ON DELETE SET NULL (client_id);

-- tax_provisions (019)
ALTER TABLE public.tax_provisions DROP CONSTRAINT IF EXISTS tax_provisions_request_fkey;
ALTER TABLE public.tax_provisions ADD CONSTRAINT tax_provisions_request_fkey
    FOREIGN KEY (organization_id, request_id)
    REFERENCES public.requests(organization_id, id) ON DELETE SET NULL (request_id);
ALTER TABLE public.tax_provisions DROP CONSTRAINT IF EXISTS tax_provisions_case_fkey;
ALTER TABLE public.tax_provisions ADD CONSTRAINT tax_provisions_case_fkey
    FOREIGN KEY (organization_id, case_id)
    REFERENCES public.cases(organization_id, id) ON DELETE SET NULL (case_id);
ALTER TABLE public.tax_provisions DROP CONSTRAINT IF EXISTS tax_provisions_client_fkey;
ALTER TABLE public.tax_provisions ADD CONSTRAINT tax_provisions_client_fkey
    FOREIGN KEY (organization_id, client_id)
    REFERENCES public.clients(organization_id, id) ON DELETE SET NULL (client_id);

-- fiscal_documents (019)
ALTER TABLE public.fiscal_documents DROP CONSTRAINT IF EXISTS fiscal_documents_request_fkey;
ALTER TABLE public.fiscal_documents ADD CONSTRAINT fiscal_documents_request_fkey
    FOREIGN KEY (organization_id, request_id)
    REFERENCES public.requests(organization_id, id) ON DELETE SET NULL (request_id);
ALTER TABLE public.fiscal_documents DROP CONSTRAINT IF EXISTS fiscal_documents_case_fkey;
ALTER TABLE public.fiscal_documents ADD CONSTRAINT fiscal_documents_case_fkey
    FOREIGN KEY (organization_id, case_id)
    REFERENCES public.cases(organization_id, id) ON DELETE SET NULL (case_id);
ALTER TABLE public.fiscal_documents DROP CONSTRAINT IF EXISTS fiscal_documents_client_fkey;
ALTER TABLE public.fiscal_documents ADD CONSTRAINT fiscal_documents_client_fkey
    FOREIGN KEY (organization_id, client_id)
    REFERENCES public.clients(organization_id, id) ON DELETE SET NULL (client_id);

-- requests.case_id → cases (008)
ALTER TABLE public.requests DROP CONSTRAINT IF EXISTS requests_case_fkey;
ALTER TABLE public.requests ADD CONSTRAINT requests_case_fkey
    FOREIGN KEY (organization_id, case_id)
    REFERENCES public.cases(organization_id, id) ON DELETE SET NULL (case_id);

-- cases.request_id → requests (008)
ALTER TABLE public.cases DROP CONSTRAINT IF EXISTS cases_request_fkey;
ALTER TABLE public.cases ADD CONSTRAINT cases_request_fkey
    FOREIGN KEY (organization_id, request_id)
    REFERENCES public.requests(organization_id, id) ON DELETE SET NULL (request_id);

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────────
-- ROLLBACK (aplicar manualmente para reverter esta migration):
--
-- BEGIN;
--   DROP INDEX IF EXISTS public.uq_fiscal_documents_org_numero;
--   CREATE UNIQUE INDEX uq_fiscal_documents_org_numero
--       ON public.fiscal_documents (organization_id, doc_type, serie, numero)
--       WHERE numero IS NOT NULL;
--   DROP INDEX IF EXISTS public.uq_fiscal_documents_org_extid;
--   CREATE UNIQUE INDEX uq_fiscal_documents_org_extid
--       ON public.fiscal_documents (organization_id, provider, external_id)
--       WHERE external_id IS NOT NULL;
--   -- e, para cada FK acima, re-adicionar com ON DELETE SET NULL (sem lista de colunas):
--   --   ALTER TABLE <t> DROP CONSTRAINT IF EXISTS <fkey>;
--   --   ALTER TABLE <t> ADD CONSTRAINT <fkey> FOREIGN KEY (organization_id, <col>)
--   --       REFERENCES public.<pai>(organization_id, id) ON DELETE SET NULL;
-- COMMIT;
