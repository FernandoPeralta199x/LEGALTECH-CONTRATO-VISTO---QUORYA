-- migrations/027_pix_webhook_events.sql
-- Dedup de webhook Pix. Provedores entregam webhooks at-least-once (podem repetir); esta
-- tabela append-only garante que o MESMO evento seja processado UMA vez (INSERT ... ON
-- CONFLICT DO NOTHING pela PK (provider, event_id)). Tabela de SISTEMA sem RLS (o webhook não tem
-- JWT/org — acessa via simple_tx). Complementa 026_pix_charges.sql.
BEGIN;

CREATE TABLE IF NOT EXISTS public.pix_webhook_events (
  event_id     text NOT NULL,               -- id único do evento dentro do provider
  provider     text NOT NULL DEFAULT 'mock',
  payment_id   text NOT NULL,
  status       text NOT NULL,
  received_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, event_id)
);

CREATE INDEX IF NOT EXISTS pix_webhook_events_payment_idx
  ON public.pix_webhook_events (payment_id);

GRANT SELECT, INSERT ON public.pix_webhook_events TO cv_app;

COMMIT;
