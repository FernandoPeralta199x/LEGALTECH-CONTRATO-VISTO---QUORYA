-- Migration 011 — colunas estruturais do PRODUTO em `cases` (Fundação V2).
-- O wizard "Novo Pedido" cria o caso com product_type/product_label/title/code etc.
-- (espelha legaltech-aws apps/api/src/models/case.py). Colunas que a referência
-- marca NOT NULL ficam NULLABLE aqui para não quebrar o create_case legado (141
-- testes); o pipeline V2 sempre as preenche. Colunas com default na referência
-- entram com DEFAULT + NOT NULL. Idempotente (ADD COLUMN IF NOT EXISTS).
BEGIN;

ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS product_type        varchar(64);
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS product_label       varchar(128);
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS title               varchar(255);
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS description         varchar(2000);
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS code                varchar(32);
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS recommendation      varchar(32);
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS source_mode         varchar(32)  NOT NULL DEFAULT 'local';
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS risk_level          varchar(20)  NOT NULL DEFAULT 'unknown';
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS progress            integer      NOT NULL DEFAULT 0;
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS is_local_simulation boolean      NOT NULL DEFAULT false;
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS submitted_at        timestamptz;
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS deleted_at          timestamptz;

CREATE INDEX IF NOT EXISTS idx_cases_org_status  ON public.cases(organization_id, status);
CREATE INDEX IF NOT EXISTS idx_cases_org_product ON public.cases(organization_id, product_type);
CREATE INDEX IF NOT EXISTS idx_cases_request_id  ON public.cases(request_id);

COMMIT;
