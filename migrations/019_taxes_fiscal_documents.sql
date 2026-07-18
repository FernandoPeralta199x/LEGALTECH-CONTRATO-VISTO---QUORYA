-- Migration 019 — tax_provisions + fiscal_documents (Fase 5: Tributos e Notas)
-- CONTROLE INTERNO. Valores INFORMATIVOS, entrada MANUAL do admin/contador.
-- REGRA (spec 12.1): o sistema NÃO calcula tributo por alíquota nem forja número
-- de nota. O único GENERATED é a divergência (confirmado - estimado), que é
-- conferência aritmética de dois valores digitados, não decisão fiscal.
-- Multi-tenant (organization_id + RLS FORCE por org) + auditoria (mecânica de 013/018).
-- Idempotente (IF NOT EXISTS / DROP ... IF EXISTS).
BEGIN;

-- ── (a) tax_provisions (tributos, spec 12.3) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tax_provisions (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL REFERENCES public.organizations(id),
    tax_type          varchar(20) NOT NULL
        CHECK (tax_type IN ('iss','pis','cofins','irpj','csll','inss','icms','das','outro')),
    -- Vínculos OPCIONAIS (venda/caso/cliente) — FKs COMPOSTAS org-scoped
    request_id        uuid,
    case_id           uuid,
    client_id         uuid,
    -- Valores INFORMATIVOS. base/rate são REFERÊNCIA; o VALOR do tributo é DIGITADO
    -- pelo admin, NUNCA derivado de base*rate (spec 12.1). bigint p/ folga.
    base_amount_cents       bigint NOT NULL DEFAULT 0
        CHECK (base_amount_cents >= 0 AND base_amount_cents <= 100000000000),
    rate_bps                integer  -- alíquota informativa em basis points (0..10000)
        CHECK (rate_bps IS NULL OR (rate_bps BETWEEN 0 AND 10000)),
    estimated_amount_cents  bigint NOT NULL DEFAULT 0
        CHECK (estimated_amount_cents >= 0 AND estimated_amount_cents <= 100000000000),
    confirmed_amount_cents  bigint
        CHECK (confirmed_amount_cents IS NULL OR
               (confirmed_amount_cents >= 0 AND confirmed_amount_cents <= 100000000000)),
    -- Conferência aritmética (confirmado - estimado), NÃO decisão fiscal.
    divergence_cents  bigint GENERATED ALWAYS AS
        (CASE WHEN confirmed_amount_cents IS NULL THEN NULL
              ELSE confirmed_amount_cents - estimated_amount_cents END) STORED,
    currency          char(3) NOT NULL DEFAULT 'BRL',
    status            varchar(20) NOT NULL DEFAULT 'estimado'
        CHECK (status IN ('estimado','confirmado','divergente','cancelado','nao_aplicavel')),
    competencia_ano   smallint NOT NULL CHECK (competencia_ano BETWEEN 2000 AND 2100),
    competencia_mes   smallint NOT NULL CHECK (competencia_mes BETWEEN 1 AND 12),
    -- Âncora do período de agregação (= 1º dia da competência, setado pelo handler).
    reference_at      timestamptz NOT NULL DEFAULT now(),
    provider          varchar(80),
    external_id       varchar(255),
    notes             varchar(500),
    source            varchar(20) NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual','derived','imported')),
    created_by        uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tax_provisions_org_id_uniq UNIQUE (organization_id, id),
    CONSTRAINT tax_provisions_request_fkey FOREIGN KEY (organization_id, request_id)
        REFERENCES public.requests(organization_id, id) ON DELETE SET NULL,
    CONSTRAINT tax_provisions_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE SET NULL,
    CONSTRAINT tax_provisions_client_fkey FOREIGN KEY (organization_id, client_id)
        REFERENCES public.clients(organization_id, id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tax_provisions_org_extid
    ON public.tax_provisions(organization_id, external_id) WHERE external_id IS NOT NULL;
ALTER TABLE public.tax_provisions OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.tax_provisions TO cv_app;
ALTER TABLE public.tax_provisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tax_provisions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tax_provisions_org_all ON public.tax_provisions;
CREATE POLICY tax_provisions_org_all ON public.tax_provisions FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
CREATE INDEX IF NOT EXISTS idx_tax_provisions_org_ref  ON public.tax_provisions(organization_id, reference_at);
CREATE INDEX IF NOT EXISTS idx_tax_provisions_org_comp ON public.tax_provisions(organization_id, competencia_ano, competencia_mes);
CREATE INDEX IF NOT EXISTS idx_tax_provisions_org_case ON public.tax_provisions(organization_id, case_id);

-- ── (b) fiscal_documents (notas, spec 12.4/12.5) ─────────────────────────────
CREATE TABLE IF NOT EXISTS public.fiscal_documents (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL REFERENCES public.organizations(id),
    doc_type          varchar(20) NOT NULL
        CHECK (doc_type IN ('nfse','nfe','nfce','recibo','outro')),
    request_id        uuid,
    case_id           uuid,
    client_id         uuid,
    -- número/série/cód. verificação vêm do PROVEDOR/prefeitura; o admin TRANSCREVE
    -- o número REAL emitido — o sistema NUNCA gera número (spec 12.5).
    numero              varchar(60),
    serie               varchar(20),
    codigo_verificacao  varchar(120),
    -- Subconjunto pragmático de 12.5. Valores casam com FinancialStatusPill.
    status            varchar(20) NOT NULL DEFAULT 'note_pending'
        CHECK (status IN ('not_required','note_pending','authorized','rejected','note_canceled')),
    issued_at         timestamptz,
    authorized_at     timestamptz,
    amount_cents        bigint NOT NULL DEFAULT 0
        CHECK (amount_cents >= 0 AND amount_cents <= 100000000000),
    tax_amount_cents    bigint NOT NULL DEFAULT 0
        CHECK (tax_amount_cents >= 0 AND tax_amount_cents <= 100000000000),
    -- custo de EMISSÃO da nota (pago ao provedor fiscal).
    issuance_cost_cents integer NOT NULL DEFAULT 0
        CHECK (issuance_cost_cents >= 0 AND issuance_cost_cents <= 100000000),
    currency          char(3) NOT NULL DEFAULT 'BRL',
    provider          varchar(80),
    document_url      varchar(1000),   -- PONTEIRO (S3/URL), não o binário
    xml_ref           varchar(1000),
    rejection_reason  varchar(500),
    reference_at      timestamptz NOT NULL DEFAULT now(),  -- âncora do período
    external_id       varchar(255),
    notes             varchar(500),
    source            varchar(20) NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual','derived','imported')),
    created_by        uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fiscal_documents_org_id_uniq UNIQUE (organization_id, id),
    CONSTRAINT fiscal_documents_request_fkey FOREIGN KEY (organization_id, request_id)
        REFERENCES public.requests(organization_id, id) ON DELETE SET NULL,
    CONSTRAINT fiscal_documents_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE SET NULL,
    CONSTRAINT fiscal_documents_client_fkey FOREIGN KEY (organization_id, client_id)
        REFERENCES public.clients(organization_id, id) ON DELETE SET NULL
);
-- não duplica a mesma NF (número real) na org; parcial p/ permitir rascunho sem número
CREATE UNIQUE INDEX IF NOT EXISTS uq_fiscal_documents_org_numero
    ON public.fiscal_documents(organization_id, doc_type, serie, numero) WHERE numero IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_fiscal_documents_org_extid
    ON public.fiscal_documents(organization_id, provider, external_id) WHERE external_id IS NOT NULL;
ALTER TABLE public.fiscal_documents OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.fiscal_documents TO cv_app;
ALTER TABLE public.fiscal_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fiscal_documents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS fiscal_documents_org_all ON public.fiscal_documents;
CREATE POLICY fiscal_documents_org_all ON public.fiscal_documents FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);
CREATE INDEX IF NOT EXISTS idx_fiscal_documents_org_ref    ON public.fiscal_documents(organization_id, reference_at);
CREATE INDEX IF NOT EXISTS idx_fiscal_documents_org_status ON public.fiscal_documents(organization_id, status);
CREATE INDEX IF NOT EXISTS idx_fiscal_documents_org_case   ON public.fiscal_documents(organization_id, case_id);

-- ── Auditoria (função table-agnostic já existe — 010:62; espelha 013/018) ────
DROP TRIGGER IF EXISTS audit_tax_provisions_insert ON public.tax_provisions;
CREATE TRIGGER audit_tax_provisions_insert AFTER INSERT ON public.tax_provisions
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('INSERT');
DROP TRIGGER IF EXISTS audit_tax_provisions_update ON public.tax_provisions;
CREATE TRIGGER audit_tax_provisions_update AFTER UPDATE ON public.tax_provisions
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('UPDATE');
DROP TRIGGER IF EXISTS audit_tax_provisions_delete ON public.tax_provisions;
CREATE TRIGGER audit_tax_provisions_delete AFTER DELETE ON public.tax_provisions
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('DELETE');
DROP TRIGGER IF EXISTS audit_fiscal_documents_insert ON public.fiscal_documents;
CREATE TRIGGER audit_fiscal_documents_insert AFTER INSERT ON public.fiscal_documents
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('INSERT');
DROP TRIGGER IF EXISTS audit_fiscal_documents_update ON public.fiscal_documents;
CREATE TRIGGER audit_fiscal_documents_update AFTER UPDATE ON public.fiscal_documents
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('UPDATE');
DROP TRIGGER IF EXISTS audit_fiscal_documents_delete ON public.fiscal_documents;
CREATE TRIGGER audit_fiscal_documents_delete AFTER DELETE ON public.fiscal_documents
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('DELETE');

COMMIT;
