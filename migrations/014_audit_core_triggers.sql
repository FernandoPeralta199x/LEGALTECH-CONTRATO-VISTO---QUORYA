-- Migration 014 — triggers de auditoria das entidades core (cases, documents)
-- GAP corrigido (auditoria Codex FE4-26-B2): estes triggers existiam apenas no schema
-- de referência / DB de dev, NUNCA nas migrações. Num deploy AWS limpo (aplicando só as
-- migrations) a auditoria de cases/documents ficaria AUSENTE. Esta migração porta os
-- triggers EXATAMENTE como na referência (docs/schema_referencia.sql):
--   cases    -> UPDATE + DELETE
--   documents-> DELETE
-- (pricing_configs já coberto pela migration 013; clients não é auditado na referência.)
-- Idempotente: DROP IF EXISTS + CREATE. A função audit.log_audit é SECURITY DEFINER (dbadmin)
-- e exige app.user_id setado (tenant_tx já o faz em toda mutação).
BEGIN;

DROP TRIGGER IF EXISTS audit_cases_update ON public.cases;
CREATE TRIGGER audit_cases_update
  AFTER UPDATE ON public.cases
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('UPDATE');

DROP TRIGGER IF EXISTS audit_cases_delete ON public.cases;
CREATE TRIGGER audit_cases_delete
  AFTER DELETE ON public.cases
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('DELETE');

DROP TRIGGER IF EXISTS audit_documents_delete ON public.documents;
CREATE TRIGGER audit_documents_delete
  AFTER DELETE ON public.documents
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('DELETE');

COMMIT;
