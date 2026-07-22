"""Handler de Pagamentos (aba do Módulo Financeiro) — projeção linha-a-linha dos
pagamentos registrados (requests com installment_plan) no período. READ-ONLY,
admin-only, RLS por org.

GET /financial/payments?period=...&page=...&page_size=...&q=... — cada pagamento é o
snapshot em requests.installment_plan; o detalhamento (método, valor cobrado, id da
cobrança, data) vem do jsonb. Sem migration de dados.
"""
import json
import logging

from src.services.database import tenant_tx
from src.services.financial import payments as pay_svc
from src.services.financial.period import resolve_range
from src.utils.context import (CallerRevoked, assert_active_admin, require_role,
                               require_user)
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import parse_pagination
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


@require_user
@require_role("admin")
def list_payments(event, context):
    """Pagamentos do período (paginados), com KPIs do conjunto e busca por venda/cliente/método."""
    user = event["user"]
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
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # SEC-02b: Financeiro é admin-only; reconsulta o papel ATUAL no banco para
            # fechar a janela de revogação (~2h) do token.
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            # KPIs do PERÍODO INTEIRO (sem q); a busca filtra só a lista/total.
            summary = pay_svc.compute_payments_summary(cur, start, end)
            items, total = pay_svc.list_payments(cur, start, end, page_size, offset, q)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except Exception as e:
        logger.error(json.dumps({"event": "FINANCIAL_PAYMENTS_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao listar pagamentos")

    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return success_response(200, "Pagamentos", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "currency": "BRL",
        "disclaimer": ("Cada pagamento é o registro de cobrança de um pedido (valor COBRADO, "
                       "já com juros/acréscimo). O período filtra pela DATA DO PAGAMENTO (fluxo "
                       "de caixa), não pela data da venda. Sem gateway real nesta fase: taxa = "
                       "R$ 0,00 e líquido = valor (a taxa real chega com o provedor de pagamento)."),
        "summary": summary,
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    })
