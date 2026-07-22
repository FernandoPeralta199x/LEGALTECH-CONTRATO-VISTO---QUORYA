-- Migration 006 — users multi-tenant.
-- 1) Converte users.id e password_resets.user_id de varchar -> uuid (FKs corretas).
-- 2) Adiciona users.organization_id (FK organizations) + backfill p/ org de sistema.
-- Pré-condição validada: public.users.id é varchar e está vazia em dev (conversão trivial).

BEGIN;

-- desfaz a FK varchar antes de mudar os tipos
ALTER TABLE public.password_resets DROP CONSTRAINT IF EXISTS password_resets_user_id_fkey;

ALTER TABLE public.users
  ALTER COLUMN id TYPE uuid USING id::uuid;
ALTER TABLE public.password_resets
  ALTER COLUMN user_id TYPE uuid USING user_id::uuid;

ALTER TABLE public.password_resets
  ADD CONSTRAINT password_resets_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

-- organization_id em users (nullable p/ backfill; NOT NULL ao final)
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS organization_id uuid;
UPDATE public.users SET organization_id = '00000000-0000-0000-0000-000000000001'
 WHERE organization_id IS NULL;
ALTER TABLE public.users ALTER COLUMN organization_id SET NOT NULL;

-- Garante que a constraint antiga seja removida antes de tentar criá-la
ALTER TABLE public.users
  DROP CONSTRAINT IF EXISTS users_organization_id_fkey;
ALTER TABLE public.users
  ADD CONSTRAINT users_organization_id_fkey
  FOREIGN KEY (organization_id) REFERENCES public.organizations(id);
CREATE INDEX IF NOT EXISTS idx_users_organization_id ON public.users(organization_id);

COMMIT;
