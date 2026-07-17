-- Migration 025 — índices de performance para a aba Vendas (projeção sobre requests).
--
-- A aba Vendas é o 1º acesso que filtra requests por org E ordena por created_at DESC
-- (as agregações antigas — overview/services/clients — só faziam SUM/count, sem ORDER
-- BY). A migration 016 já criou esse índice para cases/documents/clients "para listas
-- filtradas por org e ordenadas por created_at DESC"; aqui replicamos a convenção para
-- requests (a assimetria era a lacuna). Além disso, a nota de cada venda é buscada por
-- (organization_id, request_id) no LEFT JOIN LATERAL — e a FK composta NÃO cria índice
-- automático no Postgres.
--
-- Só CREATE INDEX IF NOT EXISTS: idempotente, sem risco de dados, sem lock longo em
-- tabelas do tamanho atual.
BEGIN;

-- Listagem de vendas: WHERE organization_id = ? AND created_at ∈ [start, end)
--                      ORDER BY created_at DESC, id DESC
CREATE INDEX IF NOT EXISTS idx_requests_org_created
    ON public.requests (organization_id, created_at DESC);

-- Nota da venda (LEFT JOIN LATERAL):
--   WHERE organization_id = ? AND request_id = ? ORDER BY created_at DESC LIMIT 1
CREATE INDEX IF NOT EXISTS idx_fiscal_documents_org_request
    ON public.fiscal_documents (organization_id, request_id, created_at DESC);

COMMIT;

-- ROLLBACK:
--   DROP INDEX IF EXISTS public.idx_requests_org_created;
--   DROP INDEX IF EXISTS public.idx_fiscal_documents_org_request;
