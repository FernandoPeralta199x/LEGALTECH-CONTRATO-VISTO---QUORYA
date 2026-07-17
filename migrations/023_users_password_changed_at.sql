-- Migration 023 — AUTH-02: revogação de sessão no reset de senha.
--
-- O JWT é stateless (iat/exp, sem jti nem store de sessão). reset_password troca o
-- password_hash mas NÃO invalidava tokens já emitidos: um token roubado emitido
-- antes do reset seguia válido até o exp (~2h) nas rotas que reconsultam o caller.
--
-- password_changed_at marca QUANDO a senha mudou. load_active_caller
-- (src/utils/context.py) rejeita (CallerRevoked → 403) tokens cujo iat seja
-- ANTERIOR a este instante. NULL = sem baseline de revogação → tokens atuais seguem
-- válidos até um reset ocorrer (retrocompatível; nenhum backfill necessário).
BEGIN;

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS password_changed_at timestamptz;

COMMIT;

-- ROLLBACK:
--   ALTER TABLE public.users DROP COLUMN IF EXISTS password_changed_at;
