-- Migration 012 — relatório/parecer do caso (SP3: revisão humana + relatório).
-- Multi-tenant (organization_id + RLS por org + FORCE). FK composta (org, case_id).
-- Um relatório por caso (UNIQUE org+case_id); regeração faz version+1 (upsert).
BEGIN;

CREATE TABLE IF NOT EXISTS public.case_reports (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     uuid NOT NULL REFERENCES public.organizations(id),
    case_id             uuid NOT NULL,
    status              varchar(40) NOT NULL DEFAULT 'draft',
    version             integer NOT NULL DEFAULT 1,
    summary             text NOT NULL DEFAULT '',
    findings            jsonb NOT NULL DEFAULT '[]'::jsonb,
    legal_risks         jsonb NOT NULL DEFAULT '[]'::jsonb,
    commercial_risks    jsonb NOT NULL DEFAULT '[]'::jsonb,
    reputational_risks  jsonb NOT NULL DEFAULT '[]'::jsonb,
    contractual_risks   jsonb NOT NULL DEFAULT '[]'::jsonb,
    missing_information  jsonb NOT NULL DEFAULT '[]'::jsonb,
    recommendation      text NOT NULL DEFAULT '',
    confidence          double precision,
    limitations         jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_refs         jsonb NOT NULL DEFAULT '[]'::jsonb,
    generated_by        uuid,
    generated_at        timestamptz,
    reviewed_by         uuid,
    reviewed_at         timestamptz,
    review_notes        text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT case_reports_org_id_uniq UNIQUE (organization_id, id),
    CONSTRAINT case_reports_org_case_uniq UNIQUE (organization_id, case_id),
    CONSTRAINT case_reports_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE CASCADE
);
ALTER TABLE public.case_reports OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.case_reports TO cv_app;
ALTER TABLE public.case_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.case_reports FORCE ROW LEVEL SECURITY;
CREATE POLICY case_reports_org_all ON public.case_reports FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
CREATE INDEX IF NOT EXISTS idx_case_reports_org_case ON public.case_reports(organization_id, case_id);

COMMIT;
