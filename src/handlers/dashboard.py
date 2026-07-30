"""Handler do Dashboard (visão geral da organização).

GET /dashboard/stats — agregados da org (RLS): contagens de casos/pedidos/
clientes, casos por status e os casos mais recentes. Espelha a tela inicial
vista na varredura do produto. Tudo filtrado por organização via RLS.
"""
import json
import logging

from src.services.database import tenant_tx
from src.utils.context import require_tela, require_user
from src.utils.helpers import error_response, success_response
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


@require_user
@require_tela("dashboard")
def get_stats(event, context):
    """Métricas do dashboard da organização autenticada. Modelo B: a tela Dashboard
    não é base do cliente_comum — só administrador/empresarial ou quem teve a aba
    liberada; require_tela reflete isso server-side (os dados já são RLS por org)."""
    user = event["user"]
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute("SELECT count(*) AS n FROM public.cases WHERE deleted_at IS NULL")
            total_cases = cur.fetchone()["n"]

            cur.execute(
                "SELECT status, count(*) AS n FROM public.cases"
                " WHERE deleted_at IS NULL GROUP BY status")
            cases_by_status = {r["status"]: r["n"] for r in cur.fetchall()}

            cur.execute("SELECT count(*) AS n FROM public.requests")
            total_requests = cur.fetchone()["n"]

            cur.execute("SELECT count(*) AS n FROM public.clients")
            total_clients = cur.fetchone()["n"]

            cur.execute(
                "SELECT id, code, title, product_type, status, risk_level, created_at"
                " FROM public.cases WHERE deleted_at IS NULL"
                " ORDER BY created_at DESC, id DESC LIMIT 5")
            recent = [{
                "id": str(r["id"]),
                "code": r["code"],
                "title": r["title"],
                "product_type": r["product_type"],
                "status": r["status"],
                "risk_level": r["risk_level"],
                "created_at": str(r["created_at"]) if r["created_at"] else None,
            } for r in cur.fetchall()]
    except Exception as e:
        logger.error(json.dumps({"event": "DASHBOARD_STATS_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao obter métricas do dashboard")

    return success_response(200, "Métricas do dashboard", {
        "total_cases": total_cases,
        "cases_by_status": cases_by_status,
        "total_requests": total_requests,
        "total_clients": total_clients,
        "recent_cases": recent,
    })
