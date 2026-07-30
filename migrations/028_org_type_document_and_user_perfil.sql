-- Migration 028 — Perfis de acesso multi-tenant (ver docs/PERFIS_ACESSO_SPEC.md)
--
-- Cada cliente externo passa a ser uma ORGANIZAÇÃO (tenant), reusando a RLS por
-- organization_id que já existe — o isolamento cliente↔cliente sai de graça.
--
--   organizations.type          operador (a firma) | empresarial (empresa, por CNPJ) | individual (pessoa, por CPF)
--   organizations.document_type  CNPJ | CPF  (o VALOR fica na coluna `document`, já existente)
--   users.perfil                 administrador | empresarial | cliente_comum
--
-- A coerência perfil<->type (administrador só em `operador`, empresarial só em
-- `empresarial`, etc.) é imposta na APLICAÇÃO (onboarding/criação de usuário) — um
-- CHECK não cruza tabelas; um trigger pode reforçar numa fase seguinte.
--
-- Backfill retrocompatível: as organizações atuais viram `operador` (via DEFAULT do
-- ADD COLUMN) e os usuários atuais viram `administrador` (todo o dado atual é interno/
-- da firma). Clientes externos só nascem pelo onboarding novo (empresarial/individual).
--
-- Idempotente (IF NOT EXISTS / DROP ... IF EXISTS). Rollback ao final.
BEGIN;

-- ── organizations: tipo do tenant + tipo de documento ────────────────────────────
ALTER TABLE public.organizations
    ADD COLUMN IF NOT EXISTS type varchar(20) NOT NULL DEFAULT 'operador';
ALTER TABLE public.organizations
    ADD COLUMN IF NOT EXISTS document_type varchar(8);

ALTER TABLE public.organizations DROP CONSTRAINT IF EXISTS organizations_type_chk;
ALTER TABLE public.organizations ADD CONSTRAINT organizations_type_chk
    CHECK (type IN ('operador','empresarial','individual'));

ALTER TABLE public.organizations DROP CONSTRAINT IF EXISTS organizations_document_type_chk;
ALTER TABLE public.organizations ADD CONSTRAINT organizations_document_type_chk
    CHECK (document_type IS NULL OR document_type IN ('CNPJ','CPF'));

-- Um documento identifica UMA org-cliente (um CNPJ = uma empresa; um CPF = um indivíduo).
-- Vale só para orgs-cliente (document_type NOT NULL); a firma/operador fica de fora.
DROP INDEX IF EXISTS public.uq_organizations_client_document;
CREATE UNIQUE INDEX uq_organizations_client_document
    ON public.organizations (document_type, document)
    WHERE document_type IS NOT NULL AND deleted_at IS NULL;

-- ── users: perfil de acesso ──────────────────────────────────────────────────────
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS perfil varchar(20);

ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_perfil_chk;
ALTER TABLE public.users ADD CONSTRAINT users_perfil_chk
    CHECK (perfil IS NULL OR perfil IN ('administrador','empresarial','cliente_comum'));

-- ── Backfill retrocompatível (o dado atual é todo interno/da firma) ───────────────
-- `type` já foi preenchido como 'operador' pelo DEFAULT do ADD COLUMN NOT NULL.
UPDATE public.users SET perfil = 'administrador' WHERE perfil IS NULL;

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────────
-- ROLLBACK (aplicar manualmente para reverter):
--
-- BEGIN;
--   DROP INDEX IF EXISTS public.uq_organizations_client_document;
--   ALTER TABLE public.organizations DROP CONSTRAINT IF EXISTS organizations_type_chk;
--   ALTER TABLE public.organizations DROP CONSTRAINT IF EXISTS organizations_document_type_chk;
--   ALTER TABLE public.organizations DROP COLUMN IF EXISTS document_type;
--   ALTER TABLE public.organizations DROP COLUMN IF EXISTS type;
--   ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_perfil_chk;
--   ALTER TABLE public.users DROP COLUMN IF EXISTS perfil;
-- COMMIT;
