-- Migration 020 — Aba Auditoria (Opção A): destrava a LEITURA da trilha por org.
--
-- audit.audit_log já tem RLS ENABLED (migration 001) mas SEM policy → default-deny
-- (o app não lê). A ESCRITA entra só via trigger audit.log_audit (SECURITY DEFINER),
-- que NÃO é afetado por RLS. cv_app já tem GRANT SELECT (só falta a policy).
--
-- Esta policy permite ao app LER apenas a auditoria da PRÓPRIA organização, no mesmo
-- contrato das demais tabelas (current_setting('app.organization_id') setado pelo
-- tenant_tx). Fora do tenant_tx (setting ausente) → NULL → nenhuma linha (fail-closed).
-- RLS não é forçada (owner/dbadmin segue livre p/ backups/manutenção), coerente com
-- o estado atual de audit.audit_log (relforcerowsecurity = false).

BEGIN;

DROP POLICY IF EXISTS audit_log_org_select ON audit.audit_log;

CREATE POLICY audit_log_org_select ON audit.audit_log
  FOR SELECT
  USING (organization_id = current_setting('app.organization_id', true)::uuid);

COMMIT;
