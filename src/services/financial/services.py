"""Serviços vendidos (agregação read-only) sobre public.requests, agrupada por
``product_type``. Fonte 100% real — product_type/product_label/total_price_cents/
payment_status são snapshot da própria venda, então NÃO precisa de join nem
migration. Cursor de ``tenant_tx`` (RLS por organização já aplicada).

Custo e margem POR SERVIÇO não têm fonte hoje (external_api_costs é por provider,
sem mapeamento produto↔custo) → o frontend os exibe honestamente como "—".
"""

# Buckets de payment_status — espelham src/services/financial/overview.py.
_RECEIVED = ["paid", "simulated"]        # dinheiro que entrou (simulated = mock pago em dev)
_PENDING = ["pending", "processing"]     # venda feita, ainda não paga
_CANCELED = ["canceled", "expired", "failed"]

_SUMMARY_SQL = """
    SELECT
      count(DISTINCT product_type)        AS services_count,
      count(*)                            AS count_all,
      count(total_price_cents)            AS count_priced,
      COALESCE(SUM(total_price_cents), 0) AS gross_cents,
      COALESCE(SUM(total_price_cents)
               FILTER (WHERE payment_status = ANY(%(received)s)), 0) AS received_cents,
      COALESCE(SUM(total_price_cents)
               FILTER (WHERE payment_status = ANY(%(pending)s)), 0)  AS pending_cents
    FROM public.requests
    WHERE created_at >= %(start)s AND created_at < %(end)s
"""

_BY_PRODUCT_SQL = """
    SELECT
      product_type,
      (array_agg(product_label ORDER BY created_at DESC, id DESC))[1] AS product_label,
      count(*)                            AS count_all,
      count(total_price_cents)            AS count_priced,
      COALESCE(SUM(total_price_cents), 0) AS gross_cents,
      COALESCE(SUM(total_price_cents)
               FILTER (WHERE payment_status = ANY(%(received)s)), 0) AS received_cents,
      COALESCE(SUM(total_price_cents)
               FILTER (WHERE payment_status = ANY(%(pending)s)), 0)  AS pending_cents,
      COALESCE(SUM(total_price_cents)
               FILTER (WHERE payment_status = ANY(%(canceled)s)), 0) AS canceled_cents
    FROM public.requests
    WHERE created_at >= %(start)s AND created_at < %(end)s
    GROUP BY product_type
    ORDER BY gross_cents DESC, product_type ASC
"""


def _params(start, end):
    return {"received": _RECEIVED, "pending": _PENDING, "canceled": _CANCELED,
            "start": start, "end": end}


def _ticket(gross: int, priced: int):
    return round(gross / priced) if priced else None


def compute_services_summary(cur, start, end) -> dict:
    """Totais de serviços no período (nº de serviços distintos, bruto, ticket)."""
    cur.execute(_SUMMARY_SQL, _params(start, end))
    r = cur.fetchone()
    gross = int(r["gross_cents"])
    return {
        "services_count": int(r["services_count"]),
        "count": int(r["count_all"]),
        "gross_cents": gross,
        "received_cents": int(r["received_cents"]),
        "pending_cents": int(r["pending_cents"]),
        "ticket_cents": _ticket(gross, int(r["count_priced"])),
    }


def services_by_product(cur, start, end) -> list:
    """Receita por serviço (GROUP BY product_type), ordenada por bruto desc."""
    cur.execute(_BY_PRODUCT_SQL, _params(start, end))
    out = []
    for row in cur.fetchall():
        gross = int(row["gross_cents"])
        out.append({
            "product_type": row["product_type"],
            "product_label": row["product_label"],
            "count": int(row["count_all"]),
            "gross_cents": gross,
            "received_cents": int(row["received_cents"]),
            "pending_cents": int(row["pending_cents"]),
            "canceled_cents": int(row["canceled_cents"]),
            "ticket_cents": _ticket(gross, int(row["count_priced"])),
        })
    return out
