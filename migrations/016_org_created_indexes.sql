-- Migration 016 — índices compostos (organization_id, created_at DESC)
-- As listas de cases/documents/clients são filtradas por organização (RLS) e
-- ordenadas por created_at DESC. Um índice composto (organization_id,
-- created_at DESC) cobre filtro + ordenação numa estrutura só, evitando sort
-- em memória conforme cada organização cresce. Os índices simples de
-- organization_id (007) seguem úteis para FKs/igualdade pura.
BEGIN;

CREATE INDEX IF NOT EXISTS idx_cases_org_created
    ON public.cases (organization_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_documents_org_created
    ON public.documents (organization_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_clients_org_created
    ON public.clients (organization_id, created_at DESC);

COMMIT;
