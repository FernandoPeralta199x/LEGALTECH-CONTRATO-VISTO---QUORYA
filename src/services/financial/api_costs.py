"""Custos de API externa (Fase 4) — agregação, listagem e persistência sobre
public.external_api_costs. Todas as funções operam sobre um cursor já em
``tenant_tx`` (RLS por organização aplicada). Valores em centavos.

`status='processado'` = custo real incorrido (entra em api_cost_cents / margem);
`status='previsto'` = estimativa (exposto à parte, NÃO reduz a margem real).

Custo AUTOMÁTICO: derivado do USO REAL das APIs externas (tabela triage_modules,
módulos efetivamente executados) × uma tarifa de custo por provedor. Quantidade =
real e automática; tarifa = configurada (a API real ainda é mock, então é uma
estimativa honesta, não um número forjado).
"""

# ── Tarifa de custo por consulta de cada API externa ─────────────────────────────
# O que PAGAMOS ao provedor por consulta (≠ do preço ao cliente do catálogo de
# pricing). PLACEHOLDERS — AJUSTAR aos contratos reais. Chave = provider canônico
# (sem o prefixo 'mock_' do dev). Admin-editável por org fica para uma próxima fase.
API_PROVIDER_TARIFF = {
    "escavador":  {"label": "Escavador",  "unit_cost_cents": 200},
    "targetdata": {"label": "TargetData", "unit_cost_cents": 150},
    "serasa":     {"label": "Serasa",     "unit_cost_cents": 300},
    "procon":     {"label": "Procon",     "unit_cost_cents": 100},
}

# Uso real por provedor: módulos de triagem executados (status 'done') no período.
# provider vem com prefixo 'mock_' em dev → canonizado. Incorrido em finished_at
# (execução); cai para started_at/created_at se nulo.
#
# PREMISSA v1: 1 consulta por módulo concluído (count). Retentativas (attempts>1),
# módulos em 'error' já cobrados e cache-first (reuso sem nova chamada) NÃO são
# refinados aqui — isso depende da cobrança real por chamada (Fase 7). Só os
# provedores da tarifa entram (IA/OCR ficam de fora até terem tarifa própria).
_USAGE_SQL = """
    SELECT regexp_replace(provider, '^mock_', '') AS prov, count(*) AS qty
    FROM public.triage_modules
    WHERE status = 'done'
      AND COALESCE(finished_at, started_at, created_at) >= %(start)s
      AND COALESCE(finished_at, started_at, created_at) < %(end)s
      AND regexp_replace(provider, '^mock_', '') = ANY(%(providers)s)
    GROUP BY prov
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


def automatic_api_costs(cur, start, end) -> dict:
    """Custo ESTIMADO das APIs derivado do USO REAL (triage_modules, status='done')
    × tarifa configurada por provedor. Quantidade = real (automática); tarifa =
    config. Retorna TODOS os provedores da tarifa (0 quando não houve uso — honesto).
    """
    providers = list(API_PROVIDER_TARIFF.keys())
    cur.execute(_USAGE_SQL, {"start": start, "end": end, "providers": providers})
    used = {row["prov"]: int(row["qty"]) for row in cur.fetchall()}
    by_provider, total, calls = [], 0, 0
    for key, meta in API_PROVIDER_TARIFF.items():
        qty = used.get(key, 0)
        cost = qty * meta["unit_cost_cents"]
        total += cost
        calls += qty
        by_provider.append({
            "provider": key,
            "provider_label": meta["label"],
            "quantity": qty,
            "unit_cost_cents": meta["unit_cost_cents"],
            "total_cents": cost,
        })
    by_provider.sort(key=lambda p: (-p["total_cents"], p["provider"]))
    return {"by_provider": by_provider, "total_cents": total, "calls": calls}


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
