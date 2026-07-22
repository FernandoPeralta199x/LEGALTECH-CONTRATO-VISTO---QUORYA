-- Migration 018 — external_api_costs (Fase 4: Gastos com APIs)
-- Ledger de custos de APIs externas (consultas CPF/CNPJ, processual, Serasa, OCR,
-- IA/LLM, e-mail, etc.). Fonte: entrada MANUAL do admin — ele registra o gasto que
-- já pagou ao provider. O TOTAL é calculado pelo BANCO (coluna GENERATED): o cliente
-- nunca envia total_cost_cents, tornando a forja impossível. Multi-tenant
-- (organization_id + RLS FORCE por org) + auditoria (mesma mecânica de 013).
-- Idempotente por construção (IF NOT EXISTS / DROP ... IF EXISTS).
BEGIN;

CREATE TABLE IF NOT EXISTS public.external_api_costs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL REFERENCES public.organizations(id),
    provider          varchar(80)  NOT NULL,           -- 'serasa','escavador','openai',...
    operation         varchar(120) NOT NULL,           -- 'consulta_cpf','ocr_page','llm_completion'
    -- Vínculos OPCIONAIS (venda/caso/cliente) — FKs COMPOSTAS org-scoped
    request_id        uuid,
    case_id           uuid,
    client_id         uuid,
    -- Quantidade x custo unitário; TOTAL calculado pelo banco (anti-forja).
    -- bigint no total evita overflow de int4 (quantity alto x unit alto).
    quantity          integer NOT NULL DEFAULT 1
                        CHECK (quantity > 0 AND quantity <= 1000000),
    unit_cost_cents   integer NOT NULL
                        CHECK (unit_cost_cents >= 0 AND unit_cost_cents <= 100000000),
    total_cost_cents  bigint GENERATED ALWAYS AS (quantity::bigint * unit_cost_cents) STORED,
    currency          char(3) NOT NULL DEFAULT 'BRL',
    status            varchar(20) NOT NULL DEFAULT 'processado'
                        CHECK (status IN ('previsto', 'processado')),
    -- incurred_at = data DO GASTO (base do período); created_at = data do registro
    incurred_at       timestamptz NOT NULL DEFAULT now(),
    external_id       varchar(255),                    -- id externo do provider / linha de fatura
    notes             varchar(500),
    -- Proveniência: 'manual' agora; 'derived'/'imported' reservam a tabela para um
    -- alimentador automático futuro (Fase 7) sem migração de dados.
    source            varchar(20) NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'derived', 'imported')),
    created_by        uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT external_api_costs_org_id_uniq UNIQUE (organization_id, id),
    CONSTRAINT external_api_costs_request_fkey FOREIGN KEY (organization_id, request_id)
        REFERENCES public.requests(organization_id, id) ON DELETE SET NULL,
    CONSTRAINT external_api_costs_case_fkey FOREIGN KEY (organization_id, case_id)
        REFERENCES public.cases(organization_id, id) ON DELETE SET NULL,
    CONSTRAINT external_api_costs_client_fkey FOREIGN KEY (organization_id, client_id)
        REFERENCES public.clients(organization_id, id) ON DELETE SET NULL
);

-- Idempotência p/ alimentador automático futuro (nunca duplica a mesma linha de fatura)
CREATE UNIQUE INDEX IF NOT EXISTS uq_external_api_costs_org_provider_extid
    ON public.external_api_costs(organization_id, provider, external_id)
    WHERE external_id IS NOT NULL;

ALTER TABLE public.external_api_costs OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.external_api_costs TO cv_app;
ALTER TABLE public.external_api_costs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.external_api_costs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS external_api_costs_org_all ON public.external_api_costs;
CREATE POLICY external_api_costs_org_all ON public.external_api_costs FOR ALL
  USING (organization_id = current_setting('app.organization_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.organization_id')::uuid);

CREATE INDEX IF NOT EXISTS idx_external_api_costs_org_incurred ON public.external_api_costs(organization_id, incurred_at);
CREATE INDEX IF NOT EXISTS idx_external_api_costs_org_provider ON public.external_api_costs(organization_id, provider);
CREATE INDEX IF NOT EXISTS idx_external_api_costs_org_case     ON public.external_api_costs(organization_id, case_id);

-- Auditoria (função table-agnostic já existe — 010:62; espelha 013_audit_pricing.sql)
DROP TRIGGER IF EXISTS audit_external_api_costs_insert ON public.external_api_costs;
CREATE TRIGGER audit_external_api_costs_insert AFTER INSERT ON public.external_api_costs
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('INSERT');
DROP TRIGGER IF EXISTS audit_external_api_costs_update ON public.external_api_costs;
CREATE TRIGGER audit_external_api_costs_update AFTER UPDATE ON public.external_api_costs
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('UPDATE');
DROP TRIGGER IF EXISTS audit_external_api_costs_delete ON public.external_api_costs;
CREATE TRIGGER audit_external_api_costs_delete AFTER DELETE ON public.external_api_costs
  FOR EACH ROW EXECUTE FUNCTION audit.log_audit('DELETE');

COMMIT;
