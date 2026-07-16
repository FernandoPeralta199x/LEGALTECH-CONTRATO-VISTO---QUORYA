"""Receita por cliente (agregação read-only) sobre public.requests, ligada ao
cliente via ``requests.case_id → cases.client_id → clients.id``. Cursor de
``tenant_tx`` (RLS por org já aplicada em requests, cases e clients).

HONESTIDADE: em V2 "o pedido pode preceder o cliente" — requests com case_id NULL
(ou case sem client_id) NÃO são atribuíveis a um cliente. Usamos LEFT JOIN e um
bucket explícito 'Sem cliente atribuído' (client_id = NULL) para que a soma por
cliente RECONCILIE com o bruto global — nunca descartamos receita em silêncio.
Sem migration (só leitura) e sem coluna nova.
"""

# Buckets de payment_status — fonte única em buckets.py (PRC-01: `simulated` (mock)
# NUNCA conta como recebido; é reportado à parte).
from src.services.financial.buckets import PENDING, RECEIVED

# LEFT JOIN preserva vendas não-atribuíveis (case_id NULL ou client_id NULL) no
# grupo cl.id IS NULL. O match por organization_id é redundante com a RLS (belt +
# suspenders) e casa com as FKs compostas (organization_id, id).
_JOIN = """
    FROM public.requests r
    LEFT JOIN public.cases c
      ON (r.organization_id = c.organization_id AND r.case_id = c.id)
    LEFT JOIN public.clients cl
      ON (c.organization_id = cl.organization_id AND c.client_id = cl.id)
    WHERE r.created_at >= %(start)s AND r.created_at < %(end)s
"""

_SUMMARY_SQL = """
    SELECT
      count(DISTINCT cl.id)                                          AS clients_count,
      count(*) FILTER (WHERE cl.id IS NULL)                          AS unassigned_count,
      count(*)                                                       AS count_all,
      count(r.total_price_cents)                                     AS count_priced,
      COALESCE(SUM(r.total_price_cents), 0)                          AS gross_cents,
      COALESCE(SUM(r.total_price_cents)
               FILTER (WHERE r.payment_status = ANY(%(received)s)), 0) AS received_cents,
      COALESCE(SUM(r.total_price_cents)
               FILTER (WHERE r.payment_status = ANY(%(pending)s)), 0)  AS pending_cents,
      COALESCE(SUM(r.total_price_cents)
               FILTER (WHERE cl.id IS NULL), 0)                      AS unassigned_gross_cents
""" + _JOIN

_BY_CLIENT_SQL = """
    SELECT
      cl.id                                 AS client_id,
      cl.legal_name                         AS client_name,
      count(*)                              AS count_all,
      count(r.total_price_cents)            AS count_priced,
      COALESCE(SUM(r.total_price_cents), 0) AS gross_cents,
      COALESCE(SUM(r.total_price_cents)
               FILTER (WHERE r.payment_status = ANY(%(received)s)), 0) AS received_cents,
      COALESCE(SUM(r.total_price_cents)
               FILTER (WHERE r.payment_status = ANY(%(pending)s)), 0)  AS pending_cents,
      max(r.created_at)                     AS last_purchase_at
""" + _JOIN + """
    GROUP BY cl.id, cl.legal_name
    -- cl.id (PK, no GROUP BY) desempata: dois clientes podem ter legal_name igual
    -- e empatar em gross → sem chave única o OFFSET duplicaria/pularia entre páginas.
    ORDER BY gross_cents DESC, client_name ASC NULLS LAST, cl.id ASC NULLS LAST
    LIMIT %(limit)s OFFSET %(offset)s
"""

# count(*) sobre os GRUPOS (inclui o grupo 'Sem cliente' como 1) para paginação.
_COUNT_SQL = ("SELECT count(*) AS n FROM (SELECT cl.id" + _JOIN +
              " GROUP BY cl.id, cl.legal_name) g")


def _params(start, end):
    return {"received": RECEIVED, "pending": PENDING, "start": start, "end": end}


def compute_clients_summary(cur, start, end) -> dict:
    """Totais por cliente no período + fatia não-atribuída (honesta)."""
    cur.execute(_SUMMARY_SQL, _params(start, end))
    r = cur.fetchone()
    gross = int(r["gross_cents"])
    priced = int(r["count_priced"])
    return {
        "clients_count": int(r["clients_count"]),
        "unassigned_count": int(r["unassigned_count"]),
        "unassigned_gross_cents": int(r["unassigned_gross_cents"]),
        "count": int(r["count_all"]),
        "gross_cents": gross,
        "received_cents": int(r["received_cents"]),
        "pending_cents": int(r["pending_cents"]),
        "ticket_cents": round(gross / priced) if priced else None,
    }


def clients_revenue(cur, start, end, limit, offset):
    """Receita por cliente, paginada. O grupo cl.id NULL = 'Sem cliente atribuído'
    (client_id/client_name = None; o frontend rotula)."""
    params = {**_params(start, end), "limit": limit, "offset": offset}
    cur.execute(_BY_CLIENT_SQL, params)
    items = []
    for row in cur.fetchall():
        gross = int(row["gross_cents"])
        priced = int(row["count_priced"])
        items.append({
            "client_id": str(row["client_id"]) if row["client_id"] else None,
            "client_name": row["client_name"],  # None => 'Sem cliente atribuído' (frontend)
            "count": int(row["count_all"]),
            "gross_cents": gross,
            "received_cents": int(row["received_cents"]),
            "pending_cents": int(row["pending_cents"]),
            "ticket_cents": round(gross / priced) if priced else None,
            "last_purchase_at": str(row["last_purchase_at"]) if row["last_purchase_at"] else None,
        })
    cur.execute(_COUNT_SQL, _params(start, end))
    total = int(cur.fetchone()["n"])
    return items, total
