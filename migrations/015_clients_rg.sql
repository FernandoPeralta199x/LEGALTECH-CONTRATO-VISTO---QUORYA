-- Migration 015 — coluna rg em clients
-- O formulário de clientes passa a exigir RG (pessoa física); o backend precisa persistir.
-- RG é PII de identidade: o serializer mascara/oculta para papel 'viewer' (como demais PII).
BEGIN;

ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS rg varchar(40);

COMMIT;
