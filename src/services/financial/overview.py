"""Agregações da visão geral financeira (puras) sobre a tabela `requests`.

`requests` é hoje o registro de vendas do sistema (total_price_cents em centavos,
payment_status, created_at, org-scoped por RLS). Esta função consolida os KPIs
que TÊM fonte de dados real; indicadores sem fonte hoje (custos de API, tributos,
notas, margem, receita líquida, atraso) retornam ``None`` — o frontend os exibe
honestamente como "sem dados", nunca como número forjado.

O cursor deve vir de ``tenant_tx`` (RLS por organização já aplicada).
"""

from src.services.financial.api_costs import compute_api_costs
from src.services.financial.buckets import CANCELED, PENDING, RECEIVED, SIMULATED
from src.services.financial.fiscal_documents import compute_fiscal_documents
from src.services.financial.taxes import compute_taxes

# PRC-01: `simulated` (pagamento mock) NUNCA conta como recebido, em NENHUM ambiente
# (RECEIVED = ["paid"], ver buckets.py). É sempre reportado à parte em `simulated_cents`
# (linha honesta "sem cobrança real"). [Nota: o gate de TRABALHO em case_lifecycle é
# env-sensível — lá `simulated` libera em dev; aqui, receita, nunca é recebido.]
_SQL = """
    SELECT
      count(*)                                  AS count_all,
      count(total_price_cents)                  AS count_priced,
      COALESCE(SUM(total_price_cents), 0)       AS gross_cents,
      COALESCE(SUM(total_price_cents)
               FILTER (WHERE payment_status = ANY(%(received)s)), 0) AS received_cents,
      COALESCE(SUM(total_price_cents)
               FILTER (WHERE payment_status = ANY(%(pending)s)), 0)  AS pending_cents,
      COALESCE(SUM(total_price_cents)
               FILTER (WHERE payment_status = ANY(%(canceled)s)), 0) AS canceled_cents,
      COALESCE(SUM(total_price_cents)
               FILTER (WHERE payment_status = ANY(%(simulated)s)), 0) AS simulated_cents,
      COALESCE(SUM(total_price_cents)
               FILTER (WHERE payment_status = 'refunded'), 0)        AS refunded_cents
    FROM public.requests
    WHERE created_at >= %(start)s AND created_at < %(end)s
"""


def compute_overview(cur, start, end) -> dict:
    """Roda a agregação sobre requests em ``[start, end)`` e devolve os KPIs.

    Valores monetários em centavos (int). KPIs sem fonte de dados = ``None``.
    """
    cur.execute(_SQL, {
        "received": RECEIVED,
        "pending": PENDING,
        "canceled": CANCELED,
        "simulated": SIMULATED,
        "start": start,
        "end": end,
    })
    r = cur.fetchone()

    count = int(r["count_all"])
    priced = int(r["count_priced"])
    gross = int(r["gross_cents"])
    ticket = round(gross / priced) if priced else None

    # Fase 4/5: custos de API, tributos e notas agora TÊM fonte → vazio = 0, não null.
    api = compute_api_costs(cur, start, end)
    taxes = compute_taxes(cur, start, end)
    fiscal = compute_fiscal_documents(cur, start, end)

    return {
        # KPIs com fonte real (requests + external_api_costs + tributos/notas):
        "count": count,
        "gross_cents": gross,
        "received_cents": int(r["received_cents"]),
        "pending_cents": int(r["pending_cents"]),
        "canceled_cents": int(r["canceled_cents"]),
        # PRC-01: pagamentos mock (simulated) reportados à parte — nunca entram no recebido.
        "simulated_cents": int(r["simulated_cents"]),
        "refunded_cents": int(r["refunded_cents"]),
        "ticket_cents": ticket,
        "api_cost_cents": api["api_cost_cents"],
        "api_cost_forecast_cents": api["api_cost_forecast_cents"],
        "tax_cents": taxes["tax_cents"],
        "tax_estimated_cents": taxes["tax_estimated_cents"],
        "tax_confirmed_cents": taxes["tax_confirmed_cents"],
        "invoices_count": fiscal["invoices_count"],
        "invoices_issuance_cost_cents": fiscal["invoices_issuance_cost_cents"],
        # KPIs sem fórmula acordada — honestamente nulos (frontend: "R$ —"):
        "overdue_cents": None,
        "net_cents": None,
        "margin_cents": None,
    }
