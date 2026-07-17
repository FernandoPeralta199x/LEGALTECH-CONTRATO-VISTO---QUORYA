-- Migration 024 — DB-08 (LGPD): minimização de PII na trilha de auditoria.
--
-- audit.log_audit (010) e audit.log_audit_org (021) gravam a LINHA INTEIRA em
-- audit.audit_log.change_before/after via row_to_json. Em public.clients isso
-- despeja CPF/CNPJ (document_number), RG (rg), e-mail (email) e telefone (phone)
-- integrais na trilha — um SEGUNDO local de armazenamento de dados sensíveis, sem
-- minimização. A trilha deve provar O QUE mudou, QUEM mudou e QUANDO, sem hospedar
-- os identificadores sensíveis em si.
--
-- Fix: redigir as chaves sensíveis do JSON auditado (to_jsonb(row) - 'chave' ...).
-- Subtrair chave inexistente é no-op → seguro para tabelas sem essas colunas
-- (fiscal_documents, requests, pricing_configs, etc.). password_hash entra por
-- defesa em profundidade (users hoje não é auditada, mas um trigger futuro não deve
-- vazar o hash). legal_name e endereço permanecem (accountability / baixa
-- sensibilidade) — a lista de chaves é o único ponto a ajustar se produto/jurídico
-- decidir redigir mais campos.
--
-- CREATE OR REPLACE preserva os corpos EXCETO as duas linhas de change_before/after.
-- SECURITY DEFINER + search_path + OWNER dbadmin re-afirmados (bypass da RLS de
-- audit_log em qualquer deploy, inclusive RDS sem superuser).
BEGIN;

-- ── audit.log_audit (dura: exige app.user_id; usada por pricing/custos/tributos/notas)
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
        to_jsonb(OLD) - 'document_number' - 'rg' - 'email' - 'phone' - 'password_hash',
        to_jsonb(NEW) - 'document_number' - 'rg' - 'email' - 'phone' - 'password_hash',
        NOW()
    );
    RETURN COALESCE(NEW, OLD);
END;
$function$;
ALTER FUNCTION audit.log_audit() OWNER TO dbadmin;

-- ── audit.log_audit_org (resiliente: user_id opcional; usada por requests/clients)
CREATE OR REPLACE FUNCTION audit.log_audit_org()
  RETURNS trigger
  LANGUAGE plpgsql
  SECURITY DEFINER
  SET search_path TO 'pg_catalog', 'public', 'audit'
AS $function$
DECLARE
    v_user_id uuid;
BEGIN
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
        to_jsonb(OLD) - 'document_number' - 'rg' - 'email' - 'phone' - 'password_hash',
        to_jsonb(NEW) - 'document_number' - 'rg' - 'email' - 'phone' - 'password_hash',
        NOW()
    );
    RETURN COALESCE(NEW, OLD);
END;
$function$;
ALTER FUNCTION audit.log_audit_org() OWNER TO dbadmin;

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────────
-- ROLLBACK: recriar ambas as funções com row_to_json(OLD)/row_to_json(NEW) no lugar
-- das expressões redigidas (corpos originais de 010 e 021), mantendo SECURITY
-- DEFINER, search_path e OWNER dbadmin.
