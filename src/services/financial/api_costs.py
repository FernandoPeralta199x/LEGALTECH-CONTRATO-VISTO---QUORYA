"""Custos de API externa (Fase 4) — agregação, listagem e persistência sobre
public.external_api_costs. Todas as funções operam sobre um cursor já em
``tenant_tx`` (RLS por organização aplicada). Valores em centavos.

`status='processado'` = custo real incorrido (entra em api_cost_cents / margem);
`status='previsto'` = estimativa (exposto à parte, NÃO reduz a margem real).
"""

_AGG_SQL = """
    SELECT
      COALESCE(SUM(total_cost_cents) FILTER (WHERE status = 'processado'), 0) AS processed_cents,
      COALESCE(SUM(total_cost_cents) FILTER (WHERE status = 'previsto'), 0)   AS forecast_cents,
      COALESCE(SUM(quantity)         FILTER (WHERE status = 'processado'), 0) AS processed_qty,
      count(*)                                                                AS count_all
    FROM public.external_api_costs
    WHERE incurred_at >= %(start)s AND incurred_at < %(end)s
"""

_BY_PROVIDER_SQL = """
    SELECT provider, COALESCE(SUM(total_cost_cents), 0) AS total_cents
    FROM public.external_api_costs
    WHERE incurred_at >= %(start)s AND incurred_at < %(end)s AND status = 'processado'
    GROUP BY provider
    ORDER BY total_cents DESC, provider ASC
"""

# Filtros opcionais via padrão "(%(x)s IS NULL OR coluna = %(x)s)".
_FILTER = (
    " WHERE incurred_at >= %(start)s AND incurred_at < %(end)s"
    "   AND (%(provider)s IS NULL OR provider = %(provider)s)"
    "   AND (%(status)s IS NULL OR status = %(status)s)"
)

_LIST_SQL = (
    "SELECT id, provider, operation, quantity, unit_cost_cents, total_cost_cents,"
    " currency, status, incurred_at, request_id, case_id, client_id,"
    " external_id, notes, created_at"
    " FROM public.external_api_costs" + _FILTER +
    " ORDER BY incurred_at DESC, id DESC LIMIT %(limit)s OFFSET %(offset)s"
)

_COUNT_SQL = "SELECT count(*) AS n FROM public.external_api_costs" + _FILTER


def compute_api_costs(cur, start, end) -> dict:
    """Agregados de custo de API no período (para os KPIs / overview)."""
    cur.execute(_AGG_SQL, {"start": start, "end": end})
    r = cur.fetchone()
    return {
        "api_cost_cents": int(r["processed_cents"]),
        "api_cost_forecast_cents": int(r["forecast_cents"]),
        "api_cost_quantity": int(r["processed_qty"]),
        "api_cost_count": int(r["count_all"]),
    }


def costs_by_provider(cur, start, end) -> list:
    """Custo processado por provedor (para o gráfico donut). Ordenado desc."""
    cur.execute(_BY_PROVIDER_SQL, {"start": start, "end": end})
    return [{"provider": row["provider"], "total_cents": int(row["total_cents"])}
            for row in cur.fetchall()]


def list_costs(cur, start, end, provider, status, limit, offset):
    """Lista paginada de custos + contagem total (para paginação)."""
    params = {"start": start, "end": end, "provider": provider,
              "status": status, "limit": limit, "offset": offset}
    cur.execute(_LIST_SQL, params)
    items = [_serialize(row) for row in cur.fetchall()]
    cur.execute(_COUNT_SQL, params)
    total = int(cur.fetchone()["n"])
    return items, total


def insert_cost(cur, org_id, created_by, data: dict) -> dict:
    """Insere um custo (data já validado/normalizado). O banco calcula o total.
    Retorna a linha serializada. FK/CHECK inválidos levantam psycopg2.IntegrityError."""
    cur.execute(
        "INSERT INTO public.external_api_costs"
        " (organization_id, created_by, provider, operation, quantity, unit_cost_cents,"
        "  status, incurred_at, request_id, case_id, client_id, external_id, notes, source)"
        " VALUES (%(org)s, %(by)s, %(provider)s, %(operation)s, %(quantity)s, %(unit)s,"
        "         %(status)s, COALESCE(%(incurred_at)s, now()), %(request_id)s, %(case_id)s,"
        "         %(client_id)s, %(external_id)s, %(notes)s, 'manual')"
        " RETURNING id, provider, operation, quantity, unit_cost_cents, total_cost_cents,"
        "           currency, status, incurred_at, request_id, case_id, client_id,"
        "           external_id, notes, created_at",
        {"org": org_id, "by": created_by, "provider": data["provider"],
         "operation": data["operation"], "quantity": data["quantity"],
         "unit": data["unit_cost_cents"], "status": data["status"],
         "incurred_at": data.get("incurred_at"), "request_id": data.get("request_id"),
         "case_id": data.get("case_id"), "client_id": data.get("client_id"),
         "external_id": data.get("external_id"), "notes": data.get("notes")},
    )
    return _serialize(cur.fetchone())


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "provider": row["provider"],
        "operation": row["operation"],
        "quantity": int(row["quantity"]),
        "unit_cost_cents": int(row["unit_cost_cents"]),
        "total_cost_cents": int(row["total_cost_cents"]),
        "currency": (row["currency"] or "BRL").strip(),
        "status": row["status"],
        "incurred_at": str(row["incurred_at"]) if row["incurred_at"] else None,
        "request_id": str(row["request_id"]) if row["request_id"] else None,
        "case_id": str(row["case_id"]) if row["case_id"] else None,
        "client_id": str(row["client_id"]) if row["client_id"] else None,
        "external_id": row["external_id"],
        "notes": row["notes"],
        "created_at": str(row["created_at"]) if row["created_at"] else None,
    }
