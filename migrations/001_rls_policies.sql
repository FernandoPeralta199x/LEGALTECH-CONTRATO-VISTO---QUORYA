-- Migration 001 — Completar policies RLS (cases, case_results, documents)
--
-- Contexto: o dump habilita RLS nessas tabelas, mas só havia `case_owner_access`
-- (FOR SELECT em cases). Sem policies de escrita, um role de app NÃO-owner tem
-- toda escrita negada (validado na Fase 0). Esta migration cria as policies
-- faltantes. O app DEVE setar app.user_id/app.user_role por transação
-- (set_config(..., true)); sem o contexto, current_setting estoura (fail-closed).
--
-- Condição base: dono (created_by/uploaded_by = app.user_id) OU admin.

-- ── cases (já existe case_owner_access FOR SELECT) ──────────────────────────────
DROP POLICY IF EXISTS case_insert ON public.cases;
CREATE POLICY case_insert ON public.cases FOR INSERT
  WITH CHECK (created_by = current_setting('app.user_id')::uuid
              OR current_setting('app.user_role') = 'admin');

DROP POLICY IF EXISTS case_update ON public.cases;
CREATE POLICY case_update ON public.cases FOR UPDATE
  USING (created_by = current_setting('app.user_id')::uuid
         OR current_setting('app.user_role') = 'admin')
  WITH CHECK (created_by = current_setting('app.user_id')::uuid
              OR current_setting('app.user_role') = 'admin');

DROP POLICY IF EXISTS case_delete ON public.cases;
CREATE POLICY case_delete ON public.cases FOR DELETE
  USING (created_by = current_setting('app.user_id')::uuid
         OR current_setting('app.user_role') = 'admin');

-- ── case_results (RLS habilitada, sem policies) ────────────────────────────────
DROP POLICY IF EXISTS case_results_select ON public.case_results;
CREATE POLICY case_results_select ON public.case_results FOR SELECT
  USING (created_by = current_setting('app.user_id')::uuid
         OR current_setting('app.user_role') = 'admin');

DROP POLICY IF EXISTS case_results_insert ON public.case_results;
CREATE POLICY case_results_insert ON public.case_results FOR INSERT
  WITH CHECK (created_by = current_setting('app.user_id')::uuid
              OR current_setting('app.user_role') = 'admin');

DROP POLICY IF EXISTS case_results_update ON public.case_results;
CREATE POLICY case_results_update ON public.case_results FOR UPDATE
  USING (created_by = current_setting('app.user_id')::uuid
         OR current_setting('app.user_role') = 'admin')
  WITH CHECK (created_by = current_setting('app.user_id')::uuid
              OR current_setting('app.user_role') = 'admin');

DROP POLICY IF EXISTS case_results_delete ON public.case_results;
CREATE POLICY case_results_delete ON public.case_results FOR DELETE
  USING (created_by = current_setting('app.user_id')::uuid
         OR current_setting('app.user_role') = 'admin');

-- ── documents (RLS habilitada, sem policies; refinar na Fase 4) ────────────────
DROP POLICY IF EXISTS documents_select ON public.documents;
CREATE POLICY documents_select ON public.documents FOR SELECT
  USING (uploaded_by = current_setting('app.user_id')::uuid
         OR current_setting('app.user_role') = 'admin');

DROP POLICY IF EXISTS documents_insert ON public.documents;
CREATE POLICY documents_insert ON public.documents FOR INSERT
  WITH CHECK (uploaded_by = current_setting('app.user_id')::uuid
              OR current_setting('app.user_role') = 'admin');

DROP POLICY IF EXISTS documents_update ON public.documents;
CREATE POLICY documents_update ON public.documents FOR UPDATE
  USING (uploaded_by = current_setting('app.user_id')::uuid
         OR current_setting('app.user_role') = 'admin')
  WITH CHECK (uploaded_by = current_setting('app.user_id')::uuid
              OR current_setting('app.user_role') = 'admin');

DROP POLICY IF EXISTS documents_delete ON public.documents;
CREATE POLICY documents_delete ON public.documents FOR DELETE
  USING (uploaded_by = current_setting('app.user_id')::uuid
         OR current_setting('app.user_role') = 'admin');

-- ── auditoria: trigger grava no audit_log como OWNER (append-only) ──────────────
-- audit.audit_log tem RLS habilitada SEM policy → o trigger, rodando como o role
-- de app não-owner, era bloqueado ao inserir (validado na Fase 1). Tornar a função
-- SECURITY DEFINER faz o trigger rodar como o owner (bypassa a RLS do audit_log);
-- assim o app NÃO precisa de INSERT direto na tabela de auditoria (mais seguro,
-- append-only). search_path fixo é boa prática para SECURITY DEFINER.
ALTER FUNCTION audit.log_audit() SECURITY DEFINER;
ALTER FUNCTION audit.log_audit() SET search_path = pg_catalog, public, audit;
