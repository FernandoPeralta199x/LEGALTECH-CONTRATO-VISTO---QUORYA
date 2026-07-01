-- Migration 013 — auditoria de pricing_configs via trigger SECURITY DEFINER
-- Fecha a pendência FE-4/#26: mudanças na config de pricing passam a registrar
-- change_before/change_after em audit.audit_log usando a função audit.log_audit()
-- (SECURITY DEFINER, owner dbadmin) — mesma mecânica de cases/documents na referência.
-- audit.audit_log tem RLS NÃO forçado; a função roda como dbadmin (owner) e contorna
-- a RLS para gravar o rastro mesmo com cv_app na transação.
BEGIN;

DROP TRIGGER IF EXISTS audit_pricing_configs_insert ON public.pricing_configs;
CREATE TRIGGER audit_pricing_configs_insert
  AFTER INSERT ON public.pricing_configs
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('INSERT');

DROP TRIGGER IF EXISTS audit_pricing_configs_update ON public.pricing_configs;
CREATE TRIGGER audit_pricing_configs_update
  AFTER UPDATE ON public.pricing_configs
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('UPDATE');

COMMIT;
