-- Migration 005 — tabela organizations (raiz do tenant) + RLS por organização.
-- A app conecta como cv_app (não-owner); FORCE RLS garante isolamento também
-- contra o owner/funções/views. O contexto app.organization_id é setado por
-- tenant_tx (ver migração 006 / database.py).

CREATE TABLE IF NOT EXISTS public.organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar(255) NOT NULL,
    document    varchar(32),
    status      varchar(30) NOT NULL DEFAULT 'active',
    metadata    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz
);

ALTER TABLE public.organizations OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.organizations TO cv_app;

ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organizations FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS org_self_select ON public.organizations;
CREATE POLICY org_self_select ON public.organizations FOR SELECT
  USING (id = current_setting('app.organization_id')::uuid);
DROP POLICY IF EXISTS org_self_insert ON public.organizations;
CREATE POLICY org_self_insert ON public.organizations FOR INSERT
  WITH CHECK (id = current_setting('app.organization_id')::uuid);
DROP POLICY IF EXISTS org_self_update ON public.organizations;
CREATE POLICY org_self_update ON public.organizations FOR UPDATE
  USING (id = current_setting('app.organization_id')::uuid)
  WITH CHECK (id = current_setting('app.organization_id')::uuid);

-- Org de sistema (id fixo) para backfill de dados legados/dev. Inserida pelo owner
-- (dbadmin) — o owner com FORCE RLS também sofre policy, então setamos o GUC.
SELECT set_config('app.organization_id', '00000000-0000-0000-0000-000000000001', false);
INSERT INTO public.organizations (id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'Organização de Sistema')
ON CONFLICT (id) DO NOTHING;
RESET app.organization_id;
