"""Vendas (projeção read-only) sobre public.requests — o registro de vendas do
sistema. Cada request É uma venda; esta camada projeta linha-a-linha (código,
cliente, serviço, valores, status, nota) para a aba Vendas.

RECONCILIA com a Visão Geral por construção: mesma fonte (requests) e mesmos buckets
de payment_status. NÃO cria tabela nem migration — um "ledger" separado duplicaria a
fonte e correria o risco de divergir dos KPIs.

- Cliente: requests.case_id → cases.client_id → clients.legal_name (LEFT JOIN). Venda
  sem caso/cliente = 'Sem cliente atribuído' (client_name NULL; o frontend rotula).
- Nota: última fiscal_documents por request_id (LEFT JOIN LATERAL, sem multiplicar a
  linha); sem nota = 'not_required' ("Sem nota").
- Desconto: o preço é base + módulos = total (sem campo de desconto no modelo) → 0;
  valor final = valor bruto = total_price_cents (honesto, sem inventar quebra).
- Status da venda: desfecho comercial DERIVADO do payment_status (paid/pending/
  simulated/canceled/refunded), distinto do detalhe em "Pagamento".

Cursor de tenant_tx (RLS por org já aplicada em requests/cases/clients/fiscal).
"""

# Buckets de payment_status — fonte única em buckets.py (PRC-01: `simulated` (mock)
# NUNCA conta como recebido; é reportado à parte).
from src.services.financial.buckets import CANCELED, PENDING, RECEIVED, SIMULATED

# FROM + LEFT JOINs (cliente via caso). O match por organization_id é redundante com a
# RLS (belt + suspenders) e casa com as FKs compostas (organization_id, id).
_FROM = """
    FROM public.requests r
    LEFT JOIN public.cases c
      ON (r.organization_id = c.organization_id AND r.case_id = c.id)
    LEFT JOIN public.clients cl
      ON (c.organization_id = cl.organization_id AND c.client_id = cl.id)
"""

# Última nota fiscal da venda (LATERAL LIMIT 1 → não multiplica a linha da venda).
_FROM_WITH_NOTE = _FROM + """
    LEFT JOIN LATERAL (
      SELECT fd.status FROM public.fiscal_documents fd
      WHERE fd.organization_id = r.organization_id AND fd.request_id = r.id
      ORDER BY fd.created_at DESC, fd.id DESC
      LIMIT 1
    ) f ON true
"""

# Janela de período (sem busca) — usada pelos KPIs, que representam SEMPRE o período
# inteiro (a busca filtra a TABELA, não os totais — honestidade: "Vendas do período").
_WHERE_PERIOD = """
    WHERE r.created_at >= %(start)s AND r.created_at < %(end)s
"""

# Busca opcional (q vazio => sem filtro). ILIKE em código/serviço/cliente — valor bind
# (sem injeção); % e _ do usuário são curingas do LIKE (comportamento aceito).
_SEARCH = """
      AND (%(q)s = ''
           OR r.code ILIKE %(like)s
           OR r.product_label ILIKE %(like)s
           OR cl.legal_name ILIKE %(like)s)
"""

_WHERE = _WHERE_PERIOD + _SEARCH

_SUMMARY_SQL = """
    SELECT
      count(*)                              AS count_all,
      count(r.total_price_cents)            AS count_priced,
      COALESCE(SUM(r.total_price_cents), 0) AS gross_cents,
      COALESCE(SUM(r.total_price_cents)
               FILTER (WHERE r.payment_status = ANY(%(received)s)), 0)  AS received_cents,
      COALESCE(SUM(r.total_price_cents)
               FILTER (WHERE r.payment_status = ANY(%(pending)s)), 0)   AS pending_cents,
      COALESCE(SUM(r.total_price_cents)
               FILTER (WHERE r.payment_status = ANY(%(canceled)s)), 0)  AS canceled_cents,
      COALESCE(SUM(r.total_price_cents)
               FILTER (WHERE r.payment_status = ANY(%(simulated)s)), 0) AS simulated_cents,
      COALESCE(SUM(r.total_price_cents)
               FILTER (WHERE r.payment_status = 'refunded'), 0)         AS refunded_cents
""" + _FROM + _WHERE_PERIOD

_LIST_SQL = """
    SELECT
      r.id, r.code, r.product_type, r.product_label,
      r.total_price_cents, r.payment_status, r.created_at,
      cl.id         AS client_id,
      cl.legal_name AS client_name,
      f.status      AS note_status
""" + _FROM_WITH_NOTE + _WHERE + """
    -- r.id (PK) desempata: created_at pode empatar → sem chave única o OFFSET
    -- duplicaria/pularia linhas entre páginas.
    ORDER BY r.created_at DESC, r.id DESC
    LIMIT %(limit)s OFFSET %(offset)s
"""

_COUNT_SQL = "SELECT count(*) AS n" + _FROM + _WHERE


def _params(start, end, q):
    q = (q or "").strip()
    return {
        "received": RECEIVED, "pending": PENDING, "canceled": CANCELED,
        "simulated": SIMULATED, "start": start, "end": end,
        "q": q, "like": f"%{q}%",
    }


def _sale_status(payment_status: str) -> str:
    """Desfecho comercial da venda derivado do payment_status (distinto do detalhe
    de pagamento). refunded/canceled são terminais; simulated fica visível (mock);
    o resto em aberto = pending."""
    if payment_status == "refunded":
        return "refunded"
    if payment_status in CANCELED:      # canceled/expired/failed
        return "canceled"
    if payment_status in RECEIVED:      # paid
        return "paid"
    if payment_status in SIMULATED:     # simulated (mock)
        return "simulated"
    return "pending"                    # pending/processing e qualquer status novo em aberto


def compute_sales_summary(cur, start, end) -> dict:
    """Totais das vendas do PERÍODO INTEIRO (os KPIs não são filtrados pela busca — ela
    filtra só a tabela). Centavos (int); ticket = bruto / vendas precificadas (ou None)."""
    cur.execute(_SUMMARY_SQL, _params(start, end, ""))
    r = cur.fetchone()
    gross = int(r["gross_cents"])
    priced = int(r["count_priced"])
    return {
        "count": int(r["count_all"]),
        "gross_cents": gross,
        "received_cents": int(r["received_cents"]),
        "pending_cents": int(r["pending_cents"]),
        "canceled_cents": int(r["canceled_cents"]),
        "simulated_cents": int(r["simulated_cents"]),
        "refunded_cents": int(r["refunded_cents"]),
        "ticket_cents": round(gross / priced) if priced else None,
    }


def list_sales(cur, start, end, limit, offset, q=""):
    """Vendas do período (paginadas, mais recentes primeiro). Retorna (items, total)."""
    cur.execute(_LIST_SQL, {**_params(start, end, q), "limit": limit, "offset": offset})
    items = []
    for row in cur.fetchall():
        # total_price_cents é nullable (venda sem snapshot de preço) → 0 honesto.
        gross = int(row["total_price_cents"]) if row["total_price_cents"] is not None else 0
        items.append({
            "id": str(row["id"]),
            "code": row["code"],
            "client_id": str(row["client_id"]) if row["client_id"] else None,
            "client_name": row["client_name"],   # None => 'Sem cliente atribuído' (frontend)
            "product_type": row["product_type"],
            "service": row["product_label"],
            "gross_cents": gross,
            "discount_cents": 0,                  # sem modelo de desconto
            "final_cents": gross,                 # = bruto (sem desconto)
            "sale_status": _sale_status(row["payment_status"]),
            "payment_status": row["payment_status"],
            "note_status": row["note_status"] or "not_required",
            "created_at": str(row["created_at"]) if row["created_at"] else None,
        })
    cur.execute(_COUNT_SQL, _params(start, end, q))
    total = int(cur.fetchone()["n"])
    return items, total
