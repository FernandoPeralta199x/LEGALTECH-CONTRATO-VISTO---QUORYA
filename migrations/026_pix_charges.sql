-- migrations/026_pix_charges.sql
-- Cobranças Pix dinâmicas (prepare — atrás do seam mock). Estado AUTORITATIVO da cobrança
-- (máquina de estados em src/services/pix/states.py), projetado em requests.payment_status
-- na confirmação. Tabela de SISTEMA sem RLS (precedente: users/password_resets via
-- simple_tx): é chaveada por (provider, payment_id) global — o webhook (autenticado por
-- ASSINATURA, sem JWT) acessa por payment_id via simple_tx, e os endpoints de usuário
-- escopam explicitamente por organization_id (a request já é verificada por RLS sob
-- tenant_tx). Espelha o estilo de 017_installments.sql.
BEGIN;

CREATE TABLE IF NOT EXISTS public.pix_charges (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id       uuid NOT NULL,
  organization_id  uuid NOT NULL,
  provider         text NOT NULL DEFAULT 'mock',
  payment_id       text NOT NULL,            -- id/txid da cobrança no provider
  status           text NOT NULL DEFAULT 'PENDING_PAYMENT',
  amount_cents     integer NOT NULL,
  currency         text NOT NULL DEFAULT 'BRL',
  pix_copy_paste   text,                     -- BR Code EMV (exibível; NUNCA logado)
  qr_code_image    text,                     -- data-URI/URL do QR (do provider); pode ser NULL
  expires_at       timestamptz,
  idempotency_key  text,
  payload_hash     text,
  paid_at          timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pix_charges_request_org_fkey
    FOREIGN KEY (organization_id, request_id)
    REFERENCES public.requests(organization_id, id),
  CONSTRAINT pix_charges_status_chk CHECK (status IN
    ('PENDING_PAYMENT', 'PAID', 'EXPIRED', 'CANCELLED', 'REFUNDED', 'PAYMENT_FAILED'))
);

-- Cobrança única no provider.
CREATE UNIQUE INDEX IF NOT EXISTS pix_charges_provider_payment_uq
  ON public.pix_charges (provider, payment_id);

-- Reserva anti-dupla-cobrança: no máximo UMA cobrança ATIVA (PENDING_PAYMENT) por request.
-- O INSERT concorrente da 2ª cobrança viola o índice => 409 (garante charge única por pedido).
CREATE UNIQUE INDEX IF NOT EXISTS pix_charges_one_active_per_request_uq
  ON public.pix_charges (request_id) WHERE status = 'PENDING_PAYMENT';

CREATE INDEX IF NOT EXISTS pix_charges_request_idx ON public.pix_charges (request_id);
CREATE INDEX IF NOT EXISTS pix_charges_org_idx ON public.pix_charges (organization_id);

GRANT SELECT, INSERT, UPDATE ON public.pix_charges TO cv_app;

COMMIT;
