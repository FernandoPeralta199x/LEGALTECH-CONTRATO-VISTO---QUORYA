"""Handler da aba Auditoria (Módulo Financeiro) — leitura READ-ONLY da trilha
audit.audit_log escopada por org, limitada aos recursos financeiros.

ADMIN-ONLY: a trilha revela quem-fez-o-quê (meta-dado sensível). GET exige
require_role('admin') — mais restrito que os demais GETs financeiros, alinhado ao
AdminGuard do frontend. A escrita da trilha continua só via trigger (não há POST).
"""
import json
import logging

from src.handlers.financial import _resolve_range
from src.services.database import tenant_tx
from src.services.financial import audit as aud
from src.utils.context import (CallerRevoked, assert_active_admin, require_role,
                               require_user)
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import parse_pagination
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


@require_user
@require_role("admin")
def list_audit(event, context):
    """Eventos de auditoria financeira no período (paginados), com KPIs por ação."""
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
    rtype = (params.get("resource_type") or "").strip() or None
    action = (params.get("action") or "").strip() or None
    if rtype is not None and rtype not in aud.FINANCIAL_RESOURCES:
        return error_response(400, "resource_type inválido")
    if action is not None and action not in aud.ACTIONS:
        return error_response(400, "action inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # Dado mais sensível do módulo: reconsulta o papel no banco (fecha a
            # janela de revogação do JWT — admin rebaixado/desativado não lê a trilha).
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            summary = aud.compute_audit_summary(cur, start, end)
            items, total = aud.list_audit_events(cur, start, end, rtype, action, page_size, offset)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except Exception as e:
        logger.error(json.dumps({"event": "AUDIT_LIST_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao listar a auditoria")
    return success_response(200, "Auditoria financeira", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "resources": list(aud.FINANCIAL_RESOURCES),
        "disclaimer": "Trilha de vendas, clientes, configuração de preço, custos de API, "
                      "tributos e notas — quem alterou, quando e o valor antes e depois.",
        "summary": summary,
        "items": items,
        "total": total,
    })
