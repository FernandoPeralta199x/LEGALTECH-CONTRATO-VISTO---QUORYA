-- Migration 008 — entidades do Pedido (wizard): requests + request_code_sequences
-- + case_parties. Todas multi-tenant (organization_id + RLS por org + FORCE).
-- FKs para users são omitidas (users.id é uuid; created_by é uuid lógico do JWT,
-- como em cases.created_by). FKs cross-table são compostas (organization_id, parent_id).
BEGIN;

-- ── requests ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.requests (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL REFERENCES public.organizations(id),
    created_by        uuid NOT NULL,
    code              varchar(32) NOT NULL,
    product_type      varchar(64) NOT NULL,
    product_label     varchar(128) NOT NULL,
    title             varchar(255) NOT NULL,
    description       text,
    status            varchar(32) NOT NULL,
    source_mode       varchar(32) NOT NULL,
    idempotency_key   varchar(255),
    case_id           uuid,
    total_price_cents integer,                              -- FIN-01: snapshot em centavos
    price_snapshot    jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT requests_org_id_uniq UNIQUE (organization_id, id),
    CONSTRAINT requests_org_idempotency_uniq UNIQUE (organization_id, idempotency_key),
    CONSTRAINT requests_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE SET NULL
);
ALTER TABLE public.requests OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.requests TO cv_app;
ALTER TABLE public.requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.requests FORCE ROW LEVEL SECURITY;
CREATE POLICY requests_org_all ON public.requests FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
CREATE INDEX IF NOT EXISTS idx_requests_org ON public.requests(organization_id);
CREATE INDEX IF NOT EXISTS idx_requests_org_status ON public.requests(organization_id, status);
CREATE INDEX IF NOT EXISTS idx_requests_org_case ON public.requests(organization_id, case_id);

-- ── cases.request_id (liga o caso ao pedido que o originou) ────────────────────
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS request_id uuid;
ALTER TABLE public.cases ADD CONSTRAINT cases_request_fkey
    FOREIGN KEY (organization_id, request_id)
    REFERENCES public.requests(organization_id, id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_cases_org_request ON public.cases(organization_id, request_id);

-- ── request_code_sequences (numeração de pedidos por org/ano) ──────────────────
CREATE TABLE IF NOT EXISTS public.request_code_sequences (
    organization_id uuid NOT NULL REFERENCES public.organizations(id),
    year            integer NOT NULL,
    next_number     integer NOT NULL DEFAULT 1,
    PRIMARY KEY (organization_id, year)
);
ALTER TABLE public.request_code_sequences OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.request_code_sequences TO cv_app;
ALTER TABLE public.request_code_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.request_code_sequences FORCE ROW LEVEL SECURITY;
CREATE POLICY request_code_sequences_org_all ON public.request_code_sequences FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);

-- ── case_parties (partes do contrato) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.case_parties (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES public.organizations(id),
    case_id         uuid NOT NULL,
    party_type      varchar(50) NOT NULL,
    name            varchar(255) NOT NULL,
    document        varchar(32),
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    CONSTRAINT case_parties_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE CASCADE
);
ALTER TABLE public.case_parties OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.case_parties TO cv_app;
ALTER TABLE public.case_parties ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.case_parties FORCE ROW LEVEL SECURITY;
CREATE POLICY case_parties_org_all ON public.case_parties FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
CREATE INDEX IF NOT EXISTS idx_case_parties_org_case ON public.case_parties(organization_id, case_id);

COMMIT;
