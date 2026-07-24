"""Handler de Recebíveis (aba do Módulo Financeiro) — vendas PENDENTES (a receber) no
período, com vencimento derivado do prazo de pagamento da organização. READ-ONLY,
admin-only, RLS por org.

GET /financial/receivables?period=...&page=...&page_size=...&q=... — recebível = venda
não paga; vencimento = data da venda + installment_config.primeiro_vencimento_dias.
Reconcilia com o 'total pendente' da Visão Geral. Sem migration.
"""
import json
import logging

from src.services.database import tenant_tx
from src.services.financial import receivables as rec_svc
from src.services.financial.period import resolve_range
from src.services.pricing.org_config import read_installment_config
from src.utils.context import (CallerRevoked, assert_active_admin, require_role,
                               require_user)
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import parse_pagination
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


@require_user
@require_role("admin")
def list_receivables(event, context):
    """Recebíveis do período (paginados), com KPIs e busca por venda/cliente."""
    user = event["user"]
    org = user["organization_id"]
    params = event.get("queryStringParameters") or {}
    try:
        start, end, period = resolve_range(params)
    except ValueError as e:
        return error_response(400, str(e))
    pg, err = parse_pagination(params)
    if err:
        return err
    page, page_size, offset = pg
    q = (params.get("q") or "").strip()

    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            # SEC-02b: Financeiro é admin-only; fecha a janela de revogação (~2h) do token.
            assert_active_admin(cur, user["user_id"], org)
            # Prazo de pagamento da org (fonte única, fail-safe); default 30 dias.
            icfg, _iver = read_installment_config(cur, org)
            term = icfg.primeiro_vencimento_dias
            summary = rec_svc.compute_receivables_summary(cur, start, end, term)
            items, total = rec_svc.list_receivables(cur, start, end, page_size, offset, term, q)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except Exception as e:
        logger.error(json.dumps({"event": "FINANCIAL_RECEIVABLES_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao listar recebíveis")

    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return success_response(200, "Recebíveis", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "currency": "BRL",
        "payment_term_days": term,
        "disclaimer": (f"Recebível = venda pendente (a receber). Vencimento = data da venda "
                       f"+ {term} dias (prazo de pagamento da organização); em atraso quando o "
                       f"vencimento já passou. Pagamentos simulados (mock) e recebidos não entram."),
        "summary": summary,
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    })
