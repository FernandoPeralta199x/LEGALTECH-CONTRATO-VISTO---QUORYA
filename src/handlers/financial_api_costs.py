"""Handlers de Gastos com APIs (Fase 4).

GET  /financial/api-costs?period=&provider=&status=&page=  — lista + agrega custos
     de API da organização (RLS). Acessível a qualquer usuário autenticado.
POST /financial/api-costs — registra um custo manual. Admin-only + reconsulta o
     papel ATUAL no banco (fecha a janela de revogação do JWT). O TOTAL é calculado
     pelo banco (coluna GENERATED) — o corpo nunca envia total.
"""
import json
import logging
from datetime import datetime

import psycopg2
from pydantic import ValidationError

from src.handlers.financial import BR_TZ, _resolve_range
from src.schemas.financial_schemas import CreateApiCostRequest
from src.services.database import tenant_tx
from src.services.financial import api_costs as svc
from src.utils.context import (CallerRevoked, assert_active_admin, require_role,
                               require_user)
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import (fmt_validation_error, parse_json_body,
                                 parse_pagination, valid_uuid)
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()

_VALID_STATUS = {"previsto", "processado"}


@require_user
@require_role("admin")
def list_api_costs(event, context):
    """Lista + agrega custos de API da organização, no período."""
    user = event["user"]
    params = event.get("queryStringParameters") or {}

    try:
        start, end, period = _resolve_range(params)
    except ValueError as e:
        return error_response(400, str(e))

    pg, err = parse_pagination(params)
    if err:
        return err
    _page, page_size, offset = pg

    provider = (params.get("provider") or "").strip() or None
    status = (params.get("status") or "").strip() or None
    if status is not None and status not in _VALID_STATUS:
        return error_response(400, "status inválido")

    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # SEC-02b: Financeiro é admin-only; fecha a janela de revogação (~2h) do token.
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            summary = svc.compute_api_costs(cur, start, end)
            by_provider = svc.costs_by_provider(cur, start, end)
            automatic = svc.automatic_api_costs(cur, start, end)
            items, total = svc.list_costs(cur, start, end, provider, status, page_size, offset)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except Exception as e:
        logger.error(json.dumps({"event": "API_COSTS_LIST_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao listar custos de API")

    return success_response(200, "Custos de API", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "currency": "BRL",
        # Gasto AUTOMÁTICO: derivado do uso real das APIs (triage_modules) × tarifa
        # configurada por provedor — estimativa honesta (quantidade real, tarifa config).
        "automatic": {
            **automatic,
            "disclaimer": "Estimado das APIs de consulta (Escavador, TargetData, Serasa, "
                          "Procon): 1 consulta por módulo executado × tarifa de custo "
                          "configurada. NÃO inclui IA/OCR. Ajuste a tarifa aos contratos.",
        },
        # Lançamentos MANUAIS avulsos (custos fora da triagem), como antes.
        "summary": {**summary, "by_provider": by_provider},
        "items": items,
        "total": total,
    })


@require_user
@require_role("admin")
def create_api_cost(event, context):
    """Registra um custo de API manual (admin-only, re-check de revogação no banco)."""
    user = event["user"]

    body, err = parse_json_body(event)
    if err:
        return err
    try:
        payload = CreateApiCostRequest(**body)
    except ValidationError as e:
        return error_response(400, fmt_validation_error(e))
    data = payload.model_dump()

    # Normaliza incurred_at (ISO; naive → interpretado no fuso do negócio, BRT).
    raw_incurred = data.get("incurred_at")
    if raw_incurred:
        try:
            dt = datetime.fromisoformat(raw_incurred.replace("Z", "+00:00"))
        except ValueError:
            return error_response(400, "incurred_at inválido")
        data["incurred_at"] = dt.replace(tzinfo=BR_TZ) if dt.tzinfo is None else dt

    # Canoniza os UUIDs de vínculo (a FK composta valida o pertencimento à org).
    for fk in ("request_id", "case_id", "client_id"):
        if data.get(fk) is not None:
            canon = valid_uuid(data[fk])
            if canon is None:
                return error_response(400, f"{fk} inválido")
            data[fk] = canon

    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            row = svc.insert_cost(cur, user["organization_id"], user["user_id"], data)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except psycopg2.errors.UniqueViolation:
        return error_response(409, "Já existe um custo com este external_id para o provedor")
    except psycopg2.IntegrityError as e:
        logger.info(json.dumps({"event": "API_COSTS_INTEGRITY", "error": type(e).__name__}))
        return error_response(400, "Vínculo (venda/caso/cliente) inválido para esta organização")
    except Exception as e:
        logger.error(json.dumps({"event": "API_COSTS_CREATE_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao registrar custo de API")

    return success_response(201, "Custo de API registrado", row)
