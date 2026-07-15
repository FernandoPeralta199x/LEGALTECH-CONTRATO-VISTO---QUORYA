"""Handler de Serviços (aba do Módulo Financeiro) — agregação READ-ONLY das vendas
(requests) por product_type. GET /financial/services (require_user, RLS por org).

Só leitura: não há entidade a criar (o serviço é o produto vendido). Custo/margem
por serviço ficam sem fonte nesta fase (o frontend exibe "—").
"""
import json
import logging

from src.handlers.financial import _resolve_range
from src.services.database import tenant_tx
from src.services.financial import services as svc
from src.utils.context import require_user
from src.utils.helpers import error_response, success_response
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


@require_user
def list_services(event, context):
    """Serviços vendidos no período (por product_type), com KPIs e detalhamento."""
    user = event["user"]
    params = event.get("queryStringParameters") or {}
    try:
        start, end, period = _resolve_range(params)
    except ValueError as e:
        return error_response(400, str(e))
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            summary = svc.compute_services_summary(cur, start, end)
            by_product = svc.services_by_product(cur, start, end)
    except Exception as e:
        logger.error(json.dumps({"event": "SERVICES_LIST_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao listar serviços")
    return success_response(200, "Serviços", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "currency": "BRL",
        "disclaimer": "Receita por serviço a partir das vendas. Custo/margem por serviço sem fonte nesta fase.",
        "summary": {**summary, "by_product": by_product},
    })
