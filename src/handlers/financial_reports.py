"""Relatório Executivo Financeiro (Fase 6) — consolida os agregados das Fases 3-5
num único DTO. O backend CALCULA (reusa os compute_* existentes); o frontend
renderiza e o usuário exporta em PDF pelo navegador (Ctrl+P). Sem cálculo novo,
sem migration, sem dependência.

GET /financial/reports/executive?period=<...>[&from=&to=]  (require_user, RLS por org)
"""
import json
import logging
from datetime import datetime, timezone

from src.services.financial.period import resolve_range
from src.services.database import tenant_tx
from src.services.financial.api_costs import compute_api_costs, costs_by_provider
from src.services.financial.fiscal_documents import (compute_fiscal_documents,
                                                     fiscal_by_status)
from src.services.financial.overview import compute_overview
from src.services.financial.taxes import compute_taxes, taxes_by_type
from src.utils.context import (CallerRevoked, assert_active_admin, require_role,
                               require_user)
from src.utils.helpers import error_response, success_response
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()

_TAX_DISCLAIMER = "Valores informativos — sujeitos a validação contábil/fiscal."
_FISCAL_DISCLAIMER = "O sistema registra notas já emitidas — não emite documento fiscal."
_REPORT_DISCLAIMERS = [
    "Relatório gerado a partir dos dados da própria organização (isolamento por RLS).",
    "Valores monetários em centavos de BRL; conversão para reais feita na exibição.",
    "Custos de API: 'processado' (incorrido) e 'previsto' (estimativa, fora da margem).",
    "Tributos e notas são registros informativos de entrada manual; o sistema não apura "
    "tributo por alíquota nem emite documento fiscal.",
    "Receita líquida, margem e valores em atraso não são exibidos por ausência de "
    "fórmula/fonte acordada (marcados '—').",
]


@require_user
@require_role("admin")
def get_executive_report(event, context):
    """DTO consolidado do relatório executivo da organização, no período."""
    user = event["user"]
    params = event.get("queryStringParameters") or {}

    try:
        start, end, period = resolve_range(params)
    except ValueError as e:
        return error_response(400, str(e))

    try:
        # Tudo na MESMA tenant_tx → RLS por org aplicada e leitura consistente.
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # SEC-02b: Financeiro é admin-only; fecha a janela de revogação (~2h) do token.
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            overview = compute_overview(cur, start, end)
            api_summary = compute_api_costs(cur, start, end)
            api_by_provider = costs_by_provider(cur, start, end)
            tax_summary = compute_taxes(cur, start, end)
            tax_by_type = taxes_by_type(cur, start, end)
            fiscal_summary = compute_fiscal_documents(cur, start, end)
            fiscal_status = fiscal_by_status(cur, start, end)  # var local ≠ função importada
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except Exception as e:
        logger.error(json.dumps({"event": "FINANCIAL_REPORT_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao gerar o relatório executivo")

    # Auditoria (spec 15.8) SEM migration: registra quem gerou + quando + filtros.
    logger.info(json.dumps({
        "event": "FINANCIAL_REPORT_GENERATED",
        "report_type": "executive",
        "organization_id": user["organization_id"],
        "user_id": user["user_id"],
        "user_email": user.get("email") or None,
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
    }))

    return success_response(200, "Relatório executivo financeiro", {
        "metadata": {
            "report_type": "executive",
            "title": "Relatório Executivo Financeiro",
            "period": period,
            "range": {"from": start.isoformat(), "to": end.isoformat()},
            "currency": "BRL",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generated_by": {
                "user_id": user["user_id"],
                "email": user.get("email") or None,
                "role": user["role"],
            },
            "filters": {
                # from/to só têm efeito em custom; fora dele o range vem do preset,
                # então não ecoamos params ignorados (evita "filtro aplicado" falso).
                "period": period,
                "from": (params.get("from") or None) if period == "custom" else None,
                "to": (params.get("to") or None) if period == "custom" else None,
            },
        },
        "overview": overview,
        "api_costs": {"summary": api_summary, "by_provider": api_by_provider},
        "taxes": {"disclaimer": _TAX_DISCLAIMER, "summary": tax_summary, "by_type": tax_by_type},
        "fiscal": {"disclaimer": _FISCAL_DISCLAIMER, "summary": fiscal_summary, "by_status": fiscal_status},
        "disclaimers": _REPORT_DISCLAIMERS,
    })
