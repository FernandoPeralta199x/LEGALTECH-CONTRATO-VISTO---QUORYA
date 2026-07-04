-- migrations/017_installments.sql
BEGIN;
ALTER TABLE public.pricing_configs
  ADD COLUMN IF NOT EXISTS installment_config jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.requests
  ADD COLUMN IF NOT EXISTS installment_plan jsonb,
  ADD COLUMN IF NOT EXISTS payment_status text NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS pricing_config_version integer;
COMMIT;
