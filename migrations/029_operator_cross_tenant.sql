-- Migration 029 — "Operar como org": leitura cross-tenant auditada (Fase 2)
-- (ver docs/PERFIS_ACESSO_SPEC.md §5)
--
-- A firma (org `operador`) precisa PROCESSAR casos de todas as orgs-cliente. A RLS
-- de `organizations`/`cases` é estritamente `= app.organization_id`, então:
--   • LER os dados de uma org-alvo  -> basta abrir a transação com
--     app.organization_id = alvo (operator_tx em database.py) — a RLS já libera.
--   • LISTAR as orgs-cliente e VALIDAR/AUDITAR a impersonação são leituras/escritas
--     CROSS-org que a RLS (corretamente) barra ao `cv_app`. Este é o caminho
--     EXPLÍCITO e controlado para isso — nunca um bypass silencioso.
--
-- Padrão: funções SECURITY DEFINER (rodam como o owner `dbadmin`, fora da RLS),
-- iguais aos triggers `audit.log_audit*`. A AUTORIDADE vem do BANCO (o ator precisa
-- ser admin ATIVO de uma org `operador`), NÃO do claim `role`/`perfil` do token —
-- defesa em profundidade: mesmo um app comprometido não consegue impersonar.
--
-- Idempotente (CREATE OR REPLACE / REVOKE+GRANT). Rollback ao final.
BEGIN;

-- ── Autoridade compartilhada: quem pode operar cross-tenant ───────────────────────
-- Retorna o user_id do operador (admin ATIVO de uma org `operador`) ou levanta
-- 42501 (insufficient_privilege -> o handler traduz para 403). Lê app.user_id do
-- contexto; durante a impersonação app.organization_id já aponta para o ALVO, por
-- isso a checagem NÃO usa a org do GUC — resolve a org real do ator pelo user_id.
CREATE OR REPLACE FUNCTION public.assert_operator_admin()
    RETURNS uuid
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE
    v_user_id uuid;
BEGIN
    BEGIN
        v_user_id := NULLIF(current_setting('app.user_id', true), '')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        v_user_id := NULL;
    END;

    IF v_user_id IS NULL OR NOT EXISTS (
        SELECT 1
        FROM public.users u
        JOIN public.organizations o ON o.id = u.organization_id
        WHERE u.id = v_user_id
          AND u.status = 'active'
          AND u.role = 'admin'
          AND o.type = 'operador'
          AND o.deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'nao autorizado: requer administrador da operadora'
            USING ERRCODE = '42501';   -- insufficient_privilege
    END IF;

    RETURN v_user_id;
END;
$function$;

-- ── Início de impersonação: valida o alvo + grava a trilha (LGPD) ─────────────────
-- Deve rodar DENTRO da transação já escopada ao alvo (app.organization_id = alvo).
-- (1) exige operador (assert_operator_admin); (2) o alvo precisa ser org-cliente
-- ativa (empresarial|individual) — senão 22023 (invalid_parameter_value -> 400);
-- (3) registra OPERATOR_IMPERSONATION na trilha da ORG-ALVO (o titular vê QUE
-- operador acessou os seus dados — transparência LGPD). Qualquer falha levanta e a
-- transação é revertida: sem acesso sem auditoria.
CREATE OR REPLACE FUNCTION audit.begin_operator_impersonation(
        p_target_org uuid,
        p_endpoint   text DEFAULT NULL)
    RETURNS void
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'audit'
AS $function$
DECLARE
    v_operator uuid;
    v_type     text;
BEGIN
    v_operator := public.assert_operator_admin();

    SELECT o.type INTO v_type
    FROM public.organizations o
    WHERE o.id = p_target_org AND o.deleted_at IS NULL;

    IF v_type IS NULL OR v_type NOT IN ('empresarial', 'individual') THEN
        RAISE EXCEPTION 'organizacao alvo invalida para operacao cross-tenant'
            USING ERRCODE = '22023';   -- invalid_parameter_value
    END IF;

    INSERT INTO audit.audit_log (
        user_id, organization_id, action, resource_type, resource_id,
        api_endpoint, created_at
    ) VALUES (
        v_operator, p_target_org, 'OPERATOR_IMPERSONATION', 'organization',
        p_target_org, p_endpoint, NOW()
    );
END;
$function$;

-- ── Listagem das orgs-cliente (só operador) ───────────────────────────────────────
-- Leitura cross-tenant controlada: retorna as orgs empresarial|individual ativas.
-- O `document` volta CRU (owner); a minimização LGPD (mascaramento) é feita no
-- handler, coerente com o restante do app.
CREATE OR REPLACE FUNCTION public.list_client_organizations()
    RETURNS TABLE (
        id            uuid,
        name          text,
        type          text,
        document      text,
        document_type text,
        status        text,
        created_at    timestamptz
    )
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
AS $function$
BEGIN
    PERFORM public.assert_operator_admin();

    RETURN QUERY
        SELECT o.id, o.name::text, o.type::text, o.document::text,
               o.document_type::text, o.status::text, o.created_at
        FROM public.organizations o
        WHERE o.type IN ('empresarial', 'individual')
          AND o.deleted_at IS NULL
        ORDER BY o.name ASC;
END;
$function$;

-- ── Permissões: só o app (cv_app) executa os dois pontos de entrada ───────────────
-- assert_operator_admin é bloco interno (chamado pelas duas acima, que rodam como o
-- owner) — fica fora do PUBLIC.
REVOKE ALL ON FUNCTION public.assert_operator_admin()               FROM PUBLIC;
REVOKE ALL ON FUNCTION audit.begin_operator_impersonation(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.list_client_organizations()           FROM PUBLIC;
GRANT EXECUTE ON FUNCTION audit.begin_operator_impersonation(uuid, text) TO cv_app;
GRANT EXECUTE ON FUNCTION public.list_client_organizations()            TO cv_app;

COMMIT;

-- ──────────────────────────────────────────────────────────────────────────────────
-- ROLLBACK (aplicar manualmente para reverter):
--
-- BEGIN;
--   DROP FUNCTION IF EXISTS public.list_client_organizations();
--   DROP FUNCTION IF EXISTS audit.begin_operator_impersonation(uuid, text);
--   DROP FUNCTION IF EXISTS public.assert_operator_admin();
-- COMMIT;
