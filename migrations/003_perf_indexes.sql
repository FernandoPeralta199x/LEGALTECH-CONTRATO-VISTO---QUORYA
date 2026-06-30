-- Migration 003 — índices de performance (validados via EXPLAIN ANALYZE em volume)
--
-- Achados (20k cases / 5k clients):
--   list_clients (WHERE status='active' ORDER BY created_at) -> Seq Scan + Sort.
--   list_cases: a RLS filtra created_by sobre o idx de created_at (Rows Removed
--     by Filter ~5000) -> escala mal quando o usuário tem poucos casos entre muitos.
--   case_results tem índice DUPLICADO em case_id (idx_case_results_case + _case_id).

-- list_cases: RLS por created_by + ORDER BY created_at DESC (índice composto)
CREATE INDEX IF NOT EXISTS idx_cases_created_by_created
    ON public.cases (created_by, created_at DESC);

-- list_clients: filtro status + ordenação por created_at
CREATE INDEX IF NOT EXISTS idx_clients_status_created
    ON public.clients (status, created_at DESC);

-- RLS por dono em case_results/documents
CREATE INDEX IF NOT EXISTS idx_case_results_created_by
    ON public.case_results (created_by);
CREATE INDEX IF NOT EXISTS idx_documents_uploaded_by
    ON public.documents (uploaded_by);

-- remove índice duplicado (idêntico a idx_case_results_case)
DROP INDEX IF EXISTS public.idx_case_results_case_id;
