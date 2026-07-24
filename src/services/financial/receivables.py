"""Recebíveis (projeção read-only) sobre public.requests — vendas PENDENTES (dinheiro
a receber, ainda não pago). Reconcilia com o KPI 'total pendente' da Visão Geral.

Recebível = venda com payment_status em (pending, processing). `simulated` (pagamento
MOCK, PRC-01 — reportado à parte, nunca é dinheiro real) e `paid` (já recebido) NÃO são
recebíveis.

Vencimento = data da venda + PRAZO de pagamento da organização
(installment_config.primeiro_vencimento_dias, default 30). Atraso = hoje − vencimento
(0 se ainda não venceu). O vencimento é DERIVADO: vendas pendentes não têm cronograma de
parcelas (o schedule só existe em pagamento registrado), então o prazo é o parâmetro de
negócio da org — decisão explícita (a Visão Geral não tinha fórmula de atraso). NÃO cria
tabela nem migration.

`term` (prazo em dias) é lido pelo handler via read_installment_config e passado aqui.
Cursor de tenant_tx (RLS por org já aplicada em requests/cases/clients).
"""

# Bucket de pendentes — fonte única em buckets.py (pending + processing; simulated fica à parte).
from src.services.financial.buckets import PENDING

_FROM = """
    FROM public.requests r
    LEFT JOIN public.cases c
      ON (r.organization_id = c.organization_id AND r.case_id = c.id)
    LEFT JOIN public.clients cl
      ON (c.organization_id = cl.organization_id AND c.client_id = cl.id)
"""

# Vencimento = data da venda + prazo (dias). date + int = date no Postgres.
_DUE = "(r.created_at::date + %(term)s)"

# Só vendas PENDENTES (a receber) no período de CRIAÇÃO da venda.
_WHERE_PERIOD = """
    WHERE r.payment_status = ANY(%(pending)s)
      AND r.created_at >= %(start)s AND r.created_at < %(end)s
"""

# Busca opcional em código da venda / cliente.
_SEARCH = """
      AND (%(q)s = '' OR r.code ILIKE %(like)s OR cl.legal_name ILIKE %(like)s)
"""

_WHERE = _WHERE_PERIOD + _SEARCH

# KPIs do PERÍODO INTEIRO (a busca filtra só a tabela). em atraso = vencido; a vencer = ainda no prazo.
_SUMMARY_SQL = f"""
    SELECT
      count(*)                                       AS count_all,
      COALESCE(SUM(r.total_price_cents), 0)          AS total_cents,
      COALESCE(SUM(r.total_price_cents) FILTER (WHERE {_DUE} <  CURRENT_DATE), 0) AS overdue_cents,
      COALESCE(SUM(r.total_price_cents) FILTER (WHERE {_DUE} >= CURRENT_DATE), 0) AS due_cents
""" + _FROM + _WHERE_PERIOD

_LIST_SQL = f"""
    SELECT
      r.id, r.code, r.total_price_cents,
      {_DUE}                                    AS due_date,
      GREATEST(0, CURRENT_DATE - {_DUE})        AS days_overdue,
      cl.id                                     AS client_id,
      cl.legal_name                             AS client_name
""" + _FROM + _WHERE + """
    -- vencimento crescente (mais urgente primeiro). Como due = created_at + prazo (const),
    -- ordenar por created_at é EQUIVALENTE e usa o índice idx_requests_org_created (025);
    -- r.id (PK) desempata → paginação estável.
    ORDER BY r.created_at ASC, r.id DESC
    LIMIT %(limit)s OFFSET %(offset)s
"""

_COUNT_SQL = "SELECT count(*) AS n" + _FROM + _WHERE


def _params(start, end, term, q):
    q = (q or "").strip()
    return {"pending": PENDING, "term": int(term), "start": start, "end": end,
            "q": q, "like": f"%{q}%"}


def compute_receivables_summary(cur, start, end, term) -> dict:
    """Totais dos recebíveis do PERÍODO INTEIRO (não filtrados pela busca). Centavos."""
    cur.execute(_SUMMARY_SQL, _params(start, end, term, ""))
    r = cur.fetchone()
    count = int(r["count_all"])
    total = int(r["total_cents"])
    return {
        "count": count,
        "total_cents": total,
        "overdue_cents": int(r["overdue_cents"]),
        "due_cents": int(r["due_cents"]),
        "average_cents": round(total / count) if count else None,
    }


def list_receivables(cur, start, end, limit, offset, term, q=""):
    """Recebíveis do período (paginados, vencimento crescente). Retorna (items, total)."""
    cur.execute(_LIST_SQL, {**_params(start, end, term, q), "limit": limit, "offset": offset})
    items = []
    for row in cur.fetchall():
        days = int(row["days_overdue"] or 0)
        items.append({
            "id": str(row["id"]),
            "code": row["code"],
            "client_id": str(row["client_id"]) if row["client_id"] else None,
            "client_name": row["client_name"],   # None => 'Sem cliente atribuído' (frontend)
            "amount_cents": int(row["total_price_cents"] or 0),
            "due_date": str(row["due_date"]) if row["due_date"] else None,
            "days_overdue": days,
            # em atraso se já venceu; senão a vencer (ambos no enum FinancialStatus).
            "status": "overdue" if days > 0 else "pending",
        })
    cur.execute(_COUNT_SQL, _params(start, end, term, q))
    total = int(cur.fetchone()["n"])
    return items, total
