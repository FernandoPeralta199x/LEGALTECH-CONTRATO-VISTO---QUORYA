-- Migration 009 — pipeline de triagem/agentes + cache externo + pricing.
-- Todas multi-tenant (organization_id + RLS por org + FORCE). FKs cross-table são
-- compostas (organization_id, parent_id). created_by/requested_by/updated_by/
-- document_id são uuid lógicos (sem FK; users.id e documents sem UNIQUE(org,id) aqui).
BEGIN;

-- ── triage_modules (módulos de triagem por caso) ──────────────────────────────
CREATE TABLE IF NOT EXISTS public.triage_modules (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES public.organizations(id),
    case_id         uuid NOT NULL,
    module_key      varchar(100) NOT NULL,
    module_label    varchar(200) NOT NULL,
    provider        varchar(100) NOT NULL,
    status          varchar(40) NOT NULL,
    source_mode     varchar(40) NOT NULL,
    required        boolean NOT NULL DEFAULT false,
    reason          text NOT NULL DEFAULT '',
    started_at      timestamptz,
    finished_at     timestamptz,
    attempts        integer NOT NULL DEFAULT 0,
    error_code      varchar(100),
    error_message   text,
    summary         text,
    result_ref      varchar(255),
    raw_result_ref  varchar(255),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT triage_modules_org_id_uniq UNIQUE (organization_id, id),
    CONSTRAINT triage_modules_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE CASCADE
);
ALTER TABLE public.triage_modules OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.triage_modules TO cv_app;
ALTER TABLE public.triage_modules ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.triage_modules FORCE ROW LEVEL SECURITY;
CREATE POLICY triage_modules_org_all ON public.triage_modules FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
CREATE INDEX IF NOT EXISTS idx_triage_modules_org_case ON public.triage_modules(organization_id, case_id);

-- ── provider_results (resultado normalizado de cada provider/módulo) ───────────
CREATE TABLE IF NOT EXISTS public.provider_results (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     uuid NOT NULL REFERENCES public.organizations(id),
    case_id             uuid NOT NULL,
    triage_module_id    uuid NOT NULL,
    provider            varchar(100) NOT NULL,
    provider_request_id varchar(255),
    source_mode         varchar(40) NOT NULL,
    status              varchar(40) NOT NULL,
    input_hash          varchar(128) NOT NULL,
    raw_result_ref      varchar(255),
    normalized_result   jsonb NOT NULL DEFAULT '{}'::jsonb,
    summary             text,
    risk_signals        jsonb NOT NULL DEFAULT '[]'::jsonb,
    confidence          double precision,
    error_code          varchar(100),
    error_message       text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT provider_results_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE CASCADE,
    CONSTRAINT provider_results_module_fkey FOREIGN KEY (organization_id, triage_module_id)
        REFERENCES public.triage_modules(organization_id, id) ON DELETE CASCADE
);
ALTER TABLE public.provider_results OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.provider_results TO cv_app;
ALTER TABLE public.provider_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_results FORCE ROW LEVEL SECURITY;
CREATE POLICY provider_results_org_all ON public.provider_results FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
CREATE INDEX IF NOT EXISTS idx_provider_results_org_case ON public.provider_results(organization_id, case_id);
CREATE INDEX IF NOT EXISTS idx_provider_results_module ON public.provider_results(triage_module_id);

-- ── agent_executions (execuções assíncronas dos agentes; idempotência por job) ─
CREATE TABLE IF NOT EXISTS public.agent_executions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES public.organizations(id),
    case_id         uuid NOT NULL,
    document_id     uuid,
    job_id          uuid NOT NULL,
    agent_type      varchar(80) NOT NULL,
    status          varchar(40) NOT NULL DEFAULT 'queued',
    attempt         integer NOT NULL DEFAULT 1,
    input_payload   jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_payload  jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message   text,
    started_at      timestamptz,
    completed_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_executions_org_job_uniq UNIQUE (organization_id, job_id),
    CONSTRAINT agent_executions_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE CASCADE
);
ALTER TABLE public.agent_executions OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.agent_executions TO cv_app;
ALTER TABLE public.agent_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_executions FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_executions_org_all ON public.agent_executions FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
CREATE INDEX IF NOT EXISTS idx_agent_executions_org_case ON public.agent_executions(organization_id, case_id);
CREATE INDEX IF NOT EXISTS idx_agent_executions_org_status ON public.agent_executions(organization_id, status);

-- ── external_queries_cache (cache de consultas a APIs externas) ────────────────
CREATE TABLE IF NOT EXISTS public.external_queries_cache (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL REFERENCES public.organizations(id),
    case_id            uuid,
    provider           varchar(80) NOT NULL,
    query_hash         varchar(128) NOT NULL,
    request_payload    jsonb NOT NULL,
    response_payload   jsonb,
    normalized_payload jsonb,
    status             varchar(30) NOT NULL DEFAULT 'pending',
    error_message      text,
    requested_by       uuid,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT external_queries_cache_uniq UNIQUE (organization_id, provider, query_hash),
    CONSTRAINT external_queries_cache_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE SET NULL
);
ALTER TABLE public.external_queries_cache OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.external_queries_cache TO cv_app;
ALTER TABLE public.external_queries_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.external_queries_cache FORCE ROW LEVEL SECURITY;
CREATE POLICY external_queries_cache_org_all ON public.external_queries_cache FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
CREATE INDEX IF NOT EXISTS idx_external_queries_cache_org_case ON public.external_queries_cache(organization_id, case_id);

-- ── pricing_configs (overrides de preço por organização) ──────────────────────
CREATE TABLE IF NOT EXISTS public.pricing_configs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL REFERENCES public.organizations(id),
    cases_limit       integer,
    product_overrides jsonb NOT NULL DEFAULT '{}'::jsonb,
    module_overrides  jsonb NOT NULL DEFAULT '{}'::jsonb,
    version           integer NOT NULL DEFAULT 1,
    notes             varchar(500),
    updated_by        uuid,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pricing_configs_org_uniq UNIQUE (organization_id)
);
ALTER TABLE public.pricing_configs OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.pricing_configs TO cv_app;
ALTER TABLE public.pricing_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pricing_configs FORCE ROW LEVEL SECURITY;
CREATE POLICY pricing_configs_org_all ON public.pricing_configs FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);

COMMIT;
