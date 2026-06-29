-- Migration 002 — corrige audit.log_audit() para eventos de DELETE
--
-- Bug (validado em runtime): o trigger de auditoria roda AFTER DELETE, mas a função
-- usa `NEW.id` e `row_to_json(NEW)`. Em DELETE, `NEW` é NULL → o `audit_log` grava
-- `resource_id = NULL` (não registra QUAL recurso foi apagado). Num LegalTech isso
-- é uma falha de trilha de auditoria/compliance.
--
-- Correção: `resource_id = COALESCE(NEW.id, OLD.id)` (DELETE usa OLD.id; INSERT/UPDATE
-- usam NEW.id) e `RETURN COALESCE(NEW, OLD)`. `change_before`/`change_after` já ficam
-- corretos (em DELETE: before = linha apagada, after = NULL). Mantém SECURITY DEFINER.

CREATE OR REPLACE FUNCTION audit.log_audit()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'pg_catalog', 'public', 'audit'
AS $function$
BEGIN
    INSERT INTO audit.audit_log (
        user_id, action, resource_type, resource_id,
        change_before, change_after, created_at
    ) VALUES (
        current_setting('app.user_id')::UUID,
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
