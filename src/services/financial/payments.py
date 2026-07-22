"""Pagamentos (projeção read-only) sobre public.requests com pagamento registrado.

Um pagamento existe quando `requests.installment_plan` foi gravado (o finalize da
reserva atômica escreve o snapshot completo, incluindo o sub-objeto `payment` do
provider). Esta camada projeta linha-a-linha o detalhe do pagamento a partir desse
jsonb — NÃO cria tabela nem migration de dados (um ledger separado duplicaria a fonte).

Do `installment_plan` extraímos: method, parcelas, valor_total_cents (valor COBRADO,
já com juros/acréscimo), e do `installment_plan->'payment'` (allowlist PCI de
adapters/payment.to_public): external_reference (id da cobrança), requested_at (data).

TAXA DE GATEWAY e LÍQUIDO: o seam de pagamento é mock (sem gateway real) e o
PaymentResult não carrega fee — honestamente `fee_cents = 0` e `net = valor`. Quando o
gateway real entrar (Fase 7, PaymentResult ganha fee), a projeção passa a lê-lo.

Cliente via requests.case_id → cases.client_id → clients (LEFT JOIN). Cursor de
tenant_tx (RLS por org já aplicada em requests/cases/clients).
"""

# Buckets de payment_status — fonte única em buckets.py (PRC-01: `simulated` (mock)
# NUNCA conta como recebido; é reportado à parte).
from src.services.financial.buckets import RECEIVED, SIMULATED

_FROM = """
    FROM public.requests r
    LEFT JOIN public.cases c
      ON (r.organization_id = c.organization_id AND r.case_id = c.id)
    LEFT JOIN public.clients cl
      ON (c.organization_id = cl.organization_id AND c.client_id = cl.id)
"""

# Data do pagamento (instante em que a cobrança foi registrada). Fallback para
# created_at se um plano antigo não trouxer requested_at — nunca perde a linha.
_PAID_AT = "COALESCE((r.installment_plan->'payment'->>'requested_at')::timestamptz, r.created_at)"

# Só vendas COM pagamento registrado (installment_plan gravado no finalize) + período.
# Bucketiza pela DATA DO PAGAMENTO (fluxo de caixa) — diferente das outras abas, que
# usam created_at: um pagamento pertence ao período em que foi FEITO. Ordenação e a
# coluna "Data" usam o mesmo instante (período/sort/coluna consistentes entre si).
_WHERE_PERIOD = f"""
    WHERE r.installment_plan IS NOT NULL
      AND {_PAID_AT} >= %(start)s AND {_PAID_AT} < %(end)s
"""

# Busca opcional (q vazio => sem filtro). ILIKE em venda(código)/cliente/método — valor
# bind (sem injeção); % e _ do usuário são curingas do LIKE (aceito).
_SEARCH = """
      AND (%(q)s = ''
           OR r.code ILIKE %(like)s
           OR cl.legal_name ILIKE %(like)s
           OR (r.installment_plan->>'method') ILIKE %(like)s)
"""

_WHERE = _WHERE_PERIOD + _SEARCH

# Valor COBRADO em centavos (com juros); fallback para o preço da venda se ausente.
_AMOUNT = "COALESCE((r.installment_plan->>'valor_total_cents')::bigint, r.total_price_cents, 0)"

# KPIs do PERÍODO INTEIRO (a busca filtra só a tabela, não os totais — honestidade).
_SUMMARY_SQL = f"""
    SELECT
      count(*)                                                       AS count_all,
      COALESCE(SUM({_AMOUNT}), 0)                                    AS total_cents,
      COALESCE(SUM({_AMOUNT}) FILTER (WHERE r.payment_status = ANY(%(received)s)), 0)  AS received_cents,
      COALESCE(SUM({_AMOUNT}) FILTER (WHERE r.payment_status = ANY(%(simulated)s)), 0) AS simulated_cents
""" + _FROM + _WHERE_PERIOD

_LIST_SQL = f"""
    SELECT
      r.id,
      r.code,
      r.payment_status,
      {_AMOUNT}                                             AS amount_cents,
      r.installment_plan->>'method'                          AS method,
      (r.installment_plan->>'parcelas')::int                 AS installments,
      r.installment_plan->'payment'->>'external_reference'   AS external_reference,
      r.installment_plan->'payment'->>'requested_at'         AS paid_at,
      cl.id                                                  AS client_id,
      cl.legal_name                                          AS client_name
""" + _FROM + _WHERE + f"""
    -- ordena pela DATA DO PAGAMENTO (mesma expressão do filtro de período); r.id (PK,
    -- único) desempata → paginação estável (OFFSET não fura nem duplica).
    ORDER BY {_PAID_AT} DESC, r.id DESC
    LIMIT %(limit)s OFFSET %(offset)s
"""

_COUNT_SQL = "SELECT count(*) AS n" + _FROM + _WHERE

# Rótulo humano do método (o frontend também tem um mapa; aqui garante consistência).
_METHOD_LABEL = {"pix": "Pix", "boleto": "Boleto", "cartao": "Cartão", "debito": "Débito"}


def _params(start, end, q):
    q = (q or "").strip()
    return {"received": RECEIVED, "simulated": SIMULATED,
            "start": start, "end": end, "q": q, "like": f"%{q}%"}


def compute_payments_summary(cur, start, end) -> dict:
    """Totais dos pagamentos do PERÍODO INTEIRO (não filtrados pela busca). Valor COBRADO
    em centavos; recebido = só 'paid'; simulado (mock) reportado à parte (PRC-01)."""
    cur.execute(_SUMMARY_SQL, _params(start, end, ""))
    r = cur.fetchone()
    count = int(r["count_all"])
    total = int(r["total_cents"])
    return {
        "count": count,
        "total_cents": total,
        "received_cents": int(r["received_cents"]),
        "simulated_cents": int(r["simulated_cents"]),
        "average_cents": round(total / count) if count else None,
    }


def list_payments(cur, start, end, limit, offset, q=""):
    """Pagamentos do período (paginados, mais recentes primeiro). Retorna (items, total)."""
    cur.execute(_LIST_SQL, {**_params(start, end, q), "limit": limit, "offset": offset})
    items = []
    for row in cur.fetchall():
        method = row["method"]
        amount = int(row["amount_cents"] or 0)
        items.append({
            "id": row["external_reference"] or str(row["id"]),
            "request_id": str(row["id"]),
            "code": row["code"],
            "client_id": str(row["client_id"]) if row["client_id"] else None,
            "client_name": row["client_name"],   # None => 'Sem cliente atribuído' (frontend)
            "method": method,
            "method_label": _METHOD_LABEL.get(method, method or "—"),
            "installments": int(row["installments"]) if row["installments"] is not None else 1,
            "amount_cents": amount,
            # Mock não tem gateway real → sem taxa; líquido = bruto. Fase 7 (gateway real) preenche.
            "fee_cents": 0,
            "net_cents": amount,
            "payment_status": row["payment_status"],
            "paid_at": row["paid_at"],
        })
    cur.execute(_COUNT_SQL, _params(start, end, q))
    total = int(cur.fetchone()["n"])
    return items, total
