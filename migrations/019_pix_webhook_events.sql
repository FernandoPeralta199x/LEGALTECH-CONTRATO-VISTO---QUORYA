-- migrations/019_pix_webhook_events.sql
-- Dedup de webhook Pix. Provedores entregam webhooks at-least-once (podem repetir); esta
-- tabela append-only garante que o MESMO evento seja processado UMA vez (INSERT ... ON
-- CONFLICT DO NOTHING pela PK event_id). Tabela de SISTEMA sem RLS (o webhook não tem
-- JWT/org — acessa via simple_tx). Espelha 017/018.
BEGIN;

CREATE TABLE IF NOT EXISTS public.pix_webhook_events (
  event_id     text PRIMARY KEY,            -- id único do evento no provider (chave de dedup)
  provider     text NOT NULL DEFAULT 'mock',
  payment_id   text NOT NULL,
  status       text NOT NULL,
  received_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pix_webhook_events_payment_idx
  ON public.pix_webhook_events (payment_id);

GRANT SELECT, INSERT ON public.pix_webhook_events TO cv_app;

COMMIT;
