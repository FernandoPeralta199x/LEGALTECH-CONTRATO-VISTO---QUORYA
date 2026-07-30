"""Handler do Financeiro (visão geral agregada por organização).

GET /financial/overview?period=<hoje|7d|mês|...> — KPIs financeiros da org (RLS),
agregados a partir de `requests` (o registro de vendas atual). Backend calcula os
totais; o frontend apenas exibe. Indicadores sem fonte de dados retornam null.
"""
import json
import logging

from src.services.database import tenant_tx
from src.services.financial.overview import compute_overview
from src.services.financial.period import resolve_range
from src.utils.context import (CallerRevoked, assert_active_admin, require_perfil, require_role,
                               require_user)
from src.utils.helpers import error_response, success_response
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


@require_user
@require_role("admin")
@require_perfil("administrador")
def get_overview(event, context):
    """KPIs financeiros agregados da organização autenticada, no período."""
    user = event["user"]
    params = event.get("queryStringParameters") or {}

    try:
        start, end, period = resolve_range(params)
    except ValueError as e:
        return error_response(400, str(e))

    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # SEC-02b: o Financeiro é admin-only. @require_role lê só o papel HISTÓRICO do
            # token; reconsulta o papel ATUAL no banco para fechar a janela de revogação (~2h).
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            kpis = compute_overview(cur, start, end)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except Exception as e:
        logger.error(json.dumps({"event": "FINANCIAL_OVERVIEW_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao obter visão financeira")

    return success_response(200, "Visão financeira", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "currency": "BRL",
        "kpis": kpis,
    })
