"""Handler de Clientes (aba do Módulo Financeiro) — receita por cliente, READ-ONLY,
agregando vendas (requests) via requests.case_id → cases.client_id → clients.
GET /financial/clients (require_user, RLS por org).

Vendas não-atribuíveis (pedido sem caso/cliente) aparecem no bucket honesto
'Sem cliente atribuído' para reconciliar com o bruto global.
"""
import json
import logging

from src.handlers.financial import _resolve_range
from src.services.database import tenant_tx
from src.services.financial import clients_revenue as clirev
from src.utils.context import require_user
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import parse_pagination
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


@require_user
def list_clients_revenue(event, context):
    """Receita por cliente no período (paginada), com KPIs e fatia não-atribuída."""
    user = event["user"]
    params = event.get("queryStringParameters") or {}
    try:
        start, end, period = _resolve_range(params)
    except ValueError as e:
        return error_response(400, str(e))
    pg, err = parse_pagination(params)
    if err:
        return err
    _p, page_size, offset = pg
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            summary = clirev.compute_clients_summary(cur, start, end)
            items, total = clirev.clients_revenue(cur, start, end, page_size, offset)
    except Exception as e:
        logger.error(json.dumps({"event": "CLIENTS_REVENUE_ERROR", "error": type(e).__name__}))
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
