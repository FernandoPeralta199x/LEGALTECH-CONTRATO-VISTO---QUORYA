"""Handler de Vendas (aba do Módulo Financeiro) — projeção linha-a-linha das vendas
(requests) no período. READ-ONLY, admin-only, RLS por org.

GET /financial/sales?period=...&page=...&page_size=...&q=... — cada request é uma
venda; reconcilia com os KPIs da Visão Geral (mesma fonte). Sem migration.
"""
import json
import logging

from src.services.database import tenant_tx
from src.services.financial import sales as sales_svc
from src.services.financial.period import resolve_range
from src.utils.context import (CallerRevoked, assert_active_admin, require_perfil, require_role,
                               require_user)
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import parse_pagination
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


@require_user
@require_role("admin")
@require_perfil("administrador")
def list_sales(event, context):
    """Vendas do período (paginadas), com KPIs do conjunto e busca por código/serviço/cliente."""
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
            summary = sales_svc.compute_sales_summary(cur, start, end)
            items, total = sales_svc.list_sales(cur, start, end, page_size, offset, q)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except Exception as e:
        logger.error(json.dumps({"event": "FINANCIAL_SALES_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao listar vendas")

    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return success_response(200, "Vendas", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "currency": "BRL",
        "disclaimer": ("Cada venda é um pedido (request). Vendas sem cliente vinculado "
                       "aparecem como 'Sem cliente atribuído'; sem modelo de desconto, o "
                       "valor final é igual ao valor bruto."),
        "summary": summary,
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    })
