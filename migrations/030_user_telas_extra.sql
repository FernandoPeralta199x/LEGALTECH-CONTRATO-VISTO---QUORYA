-- Migration 030 — Perfis: abas liberáveis por usuário (Modelo B; ver PERFIS_ACESSO_SPEC.md §8)
--
-- O ADMINISTRADOR pode liberar abas específicas a um usuário (ex.: dar "Documentos"
-- a um cliente_comum) SEM trocar o perfil dele. As telas efetivas do usuário passam a
-- ser: base do perfil (PERFIL_TELAS) ∪ telas_extra (∩ liberáveis). Telas só-firma
-- (Administração/Financeiro) NUNCA são liberáveis e continuam gateadas por perfil.
--
--   users.telas_extra  text[]  -- abas liberadas pelo admin (subconjunto de {dashboard,documentos})
--
-- Default '{}' e retrocompatível (usuários atuais não têm liberação extra; o perfil
-- administrador já vê tudo, então a coluna é no-op para eles). A validação de quais
-- valores são aceitos fica na APLICAÇÃO (LIBERATABLE_TELAS em src/utils/context.py) e
-- no endpoint admin, não num CHECK — para evoluir a lista sem migration.
--
-- Idempotente (IF NOT EXISTS). Rollback ao final.
BEGIN;

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS telas_extra text[] NOT NULL DEFAULT '{}';

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────────
-- ROLLBACK (aplicar manualmente para reverter):
--
-- BEGIN;
--   ALTER TABLE public.users DROP COLUMN IF EXISTS telas_extra;
-- COMMIT;
