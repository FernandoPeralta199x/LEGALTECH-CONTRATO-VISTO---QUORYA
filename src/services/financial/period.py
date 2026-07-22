"""Regra de domínio: fronteiras de período fiscal no fuso do negócio (BRT).

ARQ-02: antes esta regra morava em `src/handlers/financial.py` e era importada como
símbolo PRIVADO (`_resolve_range`) por 6 handlers irmãos — regra de negócio na camada
HTTP, atravessando a fronteira de handlers. Aqui ela vive na camada de serviço, com
nomes públicos, ao lado dos demais `services/financial/*`.
"""
from datetime import datetime, timedelta, timezone

VALID_PERIODS = {"today", "7d", "month", "lastMonth", "year", "custom"}

# Fuso do negócio (Brasil, BRL). O Brasil não tem horário de verão desde 2019,
# então BRT é UTC-3 fixo o ano todo — as bordas de dia/mês/ano são calculadas no
# fuso local (não em UTC, que jogaria vendas de fim de dia para o dia seguinte).
# Offset fixo evita depender do pacote tzdata (ausente no Windows/dev).
BR_TZ = timezone(timedelta(hours=-3))


def start_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def first_of_month(dt: datetime) -> datetime:
    return start_of_day(dt).replace(day=1)


def parse_day(value: str) -> datetime:
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
    return start_of_day(parsed.astimezone(BR_TZ))


def resolve_range(params: dict):
    """Deriva (start, end, period) no fuso do negócio dos query params (ValueError se inválido)."""
    period = (params.get("period") or "month").strip()
    if period not in VALID_PERIODS:
        raise ValueError("Período inválido")

    now = datetime.now(BR_TZ)
    today = start_of_day(now)
    tomorrow = today + timedelta(days=1)
    month_start = first_of_month(now)
    next_month = first_of_month(month_start + timedelta(days=32))
    prev_month = first_of_month(month_start - timedelta(days=1))
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
    start = parse_day(raw_from)
    end = parse_day(raw_to) + timedelta(days=1)
    if end <= start:
        raise ValueError("Intervalo inválido: 'to' deve ser >= 'from'")
    return start, end, period
