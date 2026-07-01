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
DROP POLICY IF EXISTS case_owner_access ON public.cases;
DROP POLICY IF EXISTS case_insert ON public.cases;
DROP POLICY IF EXISTS case_update ON public.cases;
DROP POLICY IF EXISTS case_delete ON public.cases;
CREATE POLICY cases_org_all ON public.cases FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
ALTER TABLE public.cases FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS case_results_select ON public.case_results;
DROP POLICY IF EXISTS case_results_insert ON public.case_results;
DROP POLICY IF EXISTS case_results_update ON public.case_results;
DROP POLICY IF EXISTS case_results_delete ON public.case_results;
CREATE POLICY case_results_org_all ON public.case_results FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
ALTER TABLE public.case_results FORCE ROW LEVEL SECURITY;

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
DROP POLICY IF EXISTS clients_org_all ON public.clients;
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
