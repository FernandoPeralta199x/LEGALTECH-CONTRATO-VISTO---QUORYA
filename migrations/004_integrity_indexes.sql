-- Migration 004 — integridade e índices (varredura #4)
--
-- B6: forgot_password concorrente podia deixar VÁRIOS tokens de reset válidos para
-- o mesmo usuário (schema só tinha UNIQUE em token). Garantir 1 por usuário + upsert
-- atômico no handler.

-- remove duplicatas pré-existentes (mantém a linha mais recente por ctid)
DELETE FROM public.password_resets a
USING public.password_resets b
WHERE a.user_id = b.user_id AND a.ctid < b.ctid;

CREATE UNIQUE INDEX IF NOT EXISTS uq_password_resets_user_id
    ON public.password_resets (user_id);

-- índices de listagem adicionais (evitam sort/scan em volume)
CREATE INDEX IF NOT EXISTS idx_case_results_case_created
    ON public.case_results (case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_created
    ON public.users (created_at DESC);
