-- Migration 021 — Aba Auditoria (Opção B): audita VENDAS (requests) e CLIENTES.
--
-- A função audit.log_audit (migration 001) exige app.user_id setado
-- (current_setting sem missing_ok) → QUEBRARIA qualquer escrita sem contexto de
-- usuário (seeds diretos, workers/webhooks futuros). requests é tabela quente e
-- escrita por vários caminhos → auditar NÃO pode fazer a escrita falhar.
--
-- audit.log_audit_org (nova): resiliente e org-correta —
--   * user_id: resolvido em bloco com EXCEPTION → NULL se o contexto estiver
--     AUSENTE, VAZIO ou INVÁLIDO (ex.: sentinela não-UUID 'system', ou 'None').
--     Nunca derruba a escrita-base. audit_log.user_id é nullable.
--   * organization_id: da PRÓPRIA linha (NEW/OLD.organization_id) → sempre a org
--     certa mesmo sem app.organization_id setado (a policy 020 então já isola).
--   * OWNER = dbadmin (owner de audit_log): a escrita da trilha bypassa a RLS
--     não-forçada de audit_log independentemente do role que aplicar a migration.
-- Só para tabelas que TÊM organization_id (requests, clients). As demais seguem
-- na audit.log_audit "dura" (sempre escritas via tenant_tx).

BEGIN;

CREATE OR REPLACE FUNCTION audit.log_audit_org()
  RETURNS trigger
  LANGUAGE plpgsql
  SECURITY DEFINER
  SET search_path TO 'pg_catalog', 'public', 'audit'
AS $function$
DECLARE
    v_user_id uuid;
BEGIN
    -- Ator: contexto ausente/vazio/INVÁLIDO → NULL (a auditoria NUNCA derruba a
    -- escrita-base, nem se app.user_id vier com um sentinela não-UUID).
    BEGIN
        v_user_id := NULLIF(current_setting('app.user_id', true), '')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        v_user_id := NULL;
    END;
    INSERT INTO audit.audit_log (
        user_id, organization_id, action, resource_type, resource_id,
        change_before, change_after, created_at
    ) VALUES (
        v_user_id,
        COALESCE(NEW.organization_id, OLD.organization_id),
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

-- Owner explícito: garante o bypass da RLS de audit_log em qualquer deploy (em
-- ambiente sem superuser — ex.: RDS — um owner não-privilegiado negaria o INSERT
-- na trilha e faria TODA venda/cliente dar rollback).
ALTER FUNCTION audit.log_audit_org() OWNER TO dbadmin;

-- ── requests (vendas) ─────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS audit_requests_insert ON public.requests;
CREATE TRIGGER audit_requests_insert AFTER INSERT ON public.requests
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit_org('INSERT');

DROP TRIGGER IF EXISTS audit_requests_update ON public.requests;
CREATE TRIGGER audit_requests_update AFTER UPDATE ON public.requests
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit_org('UPDATE');

DROP TRIGGER IF EXISTS audit_requests_delete ON public.requests;
CREATE TRIGGER audit_requests_delete AFTER DELETE ON public.requests
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit_org('DELETE');

-- ── clients ───────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS audit_clients_insert ON public.clients;
CREATE TRIGGER audit_clients_insert AFTER INSERT ON public.clients
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit_org('INSERT');

DROP TRIGGER IF EXISTS audit_clients_update ON public.clients;
CREATE TRIGGER audit_clients_update AFTER UPDATE ON public.clients
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit_org('UPDATE');

DROP TRIGGER IF EXISTS audit_clients_delete ON public.clients;
CREATE TRIGGER audit_clients_delete AFTER DELETE ON public.clients
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit_org('DELETE');

COMMIT;
