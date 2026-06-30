-- Migration 010 — fecha a Fundação V2 (Parte 1C):
-- (a) organization_id + RLS por org em document_chunks/document_embeddings (defesa em
--     profundidade; antes só protegidos por JOIN com documents);
-- (b) audit.audit_log ganha organization_id (rastreabilidade multi-tenant);
-- (c) nova tabela timeline_events (eventos por caso — revelada na varredura do produto).
-- Pré-condição validada: chunks/embeddings vazios; documents sem UNIQUE(org,id).
BEGIN;

-- documents precisa de UNIQUE(org,id) para as FKs compostas de chunks/embeddings
ALTER TABLE public.documents
  ADD CONSTRAINT documents_org_id_uniq UNIQUE (organization_id, id);

-- ── document_chunks ────────────────────────────────────────────────────────────
ALTER TABLE public.document_chunks ADD COLUMN IF NOT EXISTS organization_id uuid;
UPDATE public.document_chunks dc SET organization_id = d.organization_id
  FROM public.documents d WHERE dc.document_id = d.id AND dc.organization_id IS NULL;
ALTER TABLE public.document_chunks ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE public.document_chunks
  ADD CONSTRAINT document_chunks_org_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);
ALTER TABLE public.document_chunks
  ADD CONSTRAINT document_chunks_org_id_uniq UNIQUE (organization_id, id);
ALTER TABLE public.document_chunks DROP CONSTRAINT IF EXISTS document_chunks_document_id_fkey;
ALTER TABLE public.document_chunks
  ADD CONSTRAINT document_chunks_document_fkey
  FOREIGN KEY (organization_id, document_id)
  REFERENCES public.documents(organization_id, id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_document_chunks_org ON public.document_chunks(organization_id);
ALTER TABLE public.document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_chunks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS document_chunks_org_all ON public.document_chunks;
CREATE POLICY document_chunks_org_all ON public.document_chunks FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);

-- ── document_embeddings ──────────────────────────────────────────────────────────
ALTER TABLE public.document_embeddings ADD COLUMN IF NOT EXISTS organization_id uuid;
UPDATE public.document_embeddings de SET organization_id = d.organization_id
  FROM public.documents d WHERE de.document_id = d.id AND de.organization_id IS NULL;
ALTER TABLE public.document_embeddings ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE public.document_embeddings
  ADD CONSTRAINT document_embeddings_org_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);
ALTER TABLE public.document_embeddings DROP CONSTRAINT IF EXISTS document_embeddings_document_id_fkey;
ALTER TABLE public.document_embeddings
  ADD CONSTRAINT document_embeddings_document_fkey
  FOREIGN KEY (organization_id, document_id)
  REFERENCES public.documents(organization_id, id) ON DELETE CASCADE;
ALTER TABLE public.document_embeddings DROP CONSTRAINT IF EXISTS document_embeddings_chunk_id_fkey;
ALTER TABLE public.document_embeddings
  ADD CONSTRAINT document_embeddings_chunk_fkey
  FOREIGN KEY (organization_id, chunk_id)
  REFERENCES public.document_chunks(organization_id, id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_document_embeddings_org ON public.document_embeddings(organization_id);
ALTER TABLE public.document_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_embeddings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS document_embeddings_org_all ON public.document_embeddings;
CREATE POLICY document_embeddings_org_all ON public.document_embeddings FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);

-- ── audit.audit_log + organization_id (na função SECURITY DEFINER) ────────────────
ALTER TABLE audit.audit_log ADD COLUMN IF NOT EXISTS organization_id uuid;
CREATE OR REPLACE FUNCTION audit.log_audit()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'pg_catalog', 'public', 'audit'
AS $function$
BEGIN
    INSERT INTO audit.audit_log (
        user_id, organization_id, action, resource_type, resource_id,
        change_before, change_after, created_at
    ) VALUES (
        current_setting('app.user_id')::UUID,
        current_setting('app.organization_id', true)::UUID,
        TG_ARGV[0],
        TG_TABLE_NAME,
        COALESCE(NEW.id, OLD.id),
        row_to_json(OLD),
        row_to_json(NEW),
        NOW()
    );
    RETURN COALESCE(NEW, OLD);
END;
$function$;

-- ── timeline_events (linha do tempo operacional por caso) ─────────────────────────
CREATE TABLE IF NOT EXISTS public.timeline_events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES public.organizations(id),
    case_id         uuid NOT NULL,
    event_type      varchar(80) NOT NULL,
    title           varchar(255) NOT NULL,
    description     text,
    actor           varchar(20) NOT NULL DEFAULT 'system',  -- 'system' | 'user'
    actor_id        uuid,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT timeline_events_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE CASCADE
);
ALTER TABLE public.timeline_events OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.timeline_events TO cv_app;
ALTER TABLE public.timeline_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.timeline_events FORCE ROW LEVEL SECURITY;
CREATE POLICY timeline_events_org_all ON public.timeline_events FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
CREATE INDEX IF NOT EXISTS idx_timeline_events_org_case
  ON public.timeline_events(organization_id, case_id, created_at);

COMMIT;
