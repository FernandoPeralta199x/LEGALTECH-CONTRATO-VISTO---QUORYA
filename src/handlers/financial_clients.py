"""Handler de Clientes (aba do Módulo Financeiro) — receita por cliente, READ-ONLY,
agregando vendas (requests) via requests.case_id → cases.client_id → clients.
GET /financial/clients (require_user, RLS por org).

Vendas não-atribuíveis (pedido sem caso/cliente) aparecem no bucket honesto
'Sem cliente atribuído' para reconciliar com o bruto global.
"""
import json
import logging

from src.services.financial.period import resolve_range
from src.services.database import tenant_tx
from src.services.financial import clients_revenue as clirev
from src.utils.context import (CallerRevoked, assert_active_admin, require_role,
                               require_user)
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import parse_pagination
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


@require_user
@require_role("admin")
def list_clients_revenue(event, context):
    """Receita por cliente no período (paginada), com KPIs e fatia não-atribuída."""
    user = event["user"]
    params = event.get("queryStringParameters") or {}
    try:
        start, end, period = resolve_range(params)
    except ValueError as e:
        return error_response(400, str(e))
    pg, err = parse_pagination(params)
    if err:
        return err
    _p, page_size, offset = pg
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # SEC-02b: Financeiro é admin-only; fecha a janela de revogação (~2h) do token.
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            summary = clirev.compute_clients_summary(cur, start, end)
            items, total = clirev.clients_revenue(cur, start, end, page_size, offset)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except Exception as e:
        logger.error(json.dumps({"event": "CLIENTS_REVENUE_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao listar clientes")
    return success_response(200, "Clientes", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "currency": "BRL",
        "disclaimer": "Receita por cliente a partir das vendas. Vendas sem cliente vinculado aparecem em 'Sem cliente atribuído'.",
        "summary": summary,
        "items": items,
        "total": total,
    })
