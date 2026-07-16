"""Handler do Financeiro (visão geral agregada por organização).

GET /financial/overview?period=<hoje|7d|mês|...> — KPIs financeiros da org (RLS),
agregados a partir de `requests` (o registro de vendas atual). Backend calcula os
totais; o frontend apenas exibe. Indicadores sem fonte de dados retornam null.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from src.services.database import tenant_tx
from src.services.financial.overview import compute_overview
from src.utils.context import (CallerRevoked, assert_active_admin, require_role,
                               require_user)
from src.utils.helpers import error_response, success_response
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()

_VALID_PERIODS = {"today", "7d", "month", "lastMonth", "year", "custom"}

# Fuso do negócio (Brasil, BRL). O Brasil não tem horário de verão desde 2019,
# então BRT é UTC-3 fixo o ano todo — as bordas de dia/mês/ano são calculadas no
# fuso local (não em UTC, que jogaria vendas de fim de dia para o dia seguinte).
# Offset fixo evita depender do pacote tzdata (ausente no Windows/dev).
BR_TZ = timezone(timedelta(hours=-3))


def _start_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _first_of_month(dt: datetime) -> datetime:
    return _start_of_day(dt).replace(day=1)


def _parse_day(value: str) -> datetime:
    """'YYYY-MM-DD' (ou ISO) -> início do dia no fuso do negócio. ValueError se inválido."""
    text = (value or "").strip()
    if not text:
        raise ValueError("Data inválida")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # não vaza a mensagem da stdlib (que ecoa o input) — mensagem própria
        raise ValueError("Data inválida")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BR_TZ)
    return _start_of_day(parsed.astimezone(BR_TZ))


def _resolve_range(params: dict):
    """Deriva (start, end, period) no fuso do negócio dos query params (ValueError se inválido)."""
    period = (params.get("period") or "month").strip()
    if period not in _VALID_PERIODS:
        raise ValueError("Período inválido")

    now = datetime.now(BR_TZ)
    today = _start_of_day(now)
    tomorrow = today + timedelta(days=1)
    month_start = _first_of_month(now)
    next_month = _first_of_month(month_start + timedelta(days=32))
    prev_month = _first_of_month(month_start - timedelta(days=1))
    year_start = today.replace(month=1, day=1)
    next_year = year_start.replace(year=year_start.year + 1)

    if period == "today":
        return today, tomorrow, period
    if period == "7d":
        return today - timedelta(days=6), tomorrow, period
    if period == "month":
        return month_start, next_month, period
    if period == "lastMonth":
        return prev_month, month_start, period
    if period == "year":
        return year_start, next_year, period

    # custom: exige from/to (o `to` é inclusivo do dia informado)
    raw_from = params.get("from")
    raw_to = params.get("to")
    if not raw_from or not raw_to:
        raise ValueError("Período personalizado exige 'from' e 'to'")
    start = _parse_day(raw_from)
    end = _parse_day(raw_to) + timedelta(days=1)
    if end <= start:
        raise ValueError("Intervalo inválido: 'to' deve ser >= 'from'")
    return start, end, period


@require_user
@require_role("admin")
def get_overview(event, context):
    """KPIs financeiros agregados da organização autenticada, no período."""
    user = event["user"]
    params = event.get("queryStringParameters") or {}

    try:
        start, end, period = _resolve_range(params)
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
        logger.error(json.dumps({"event": "FINANCIAL_OVERVIEW_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao obter visão financeira")

    return success_response(200, "Visão financeira", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "currency": "BRL",
        "kpis": kpis,
    })
