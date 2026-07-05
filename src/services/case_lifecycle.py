"""Invariante de ciclo de vida do caso (CVS-007).

Bloqueia ESCRITA em recursos filhos (results, parties, review de relatório)
quando o caso está finalizado (`completed`/`closed`) ou soft-deletado.
Centraliza a regra que antes existia só no create de case_results.

Usar sempre com o cursor da ``tenant_tx`` corrente (respeita a RLS por org).
"""
import os

_FINALIZED_STATUSES = ("completed", "closed")
# Pagamento aceito para liberar trabalho pago (triagem/relatório).
_PAID_STATUSES = ("simulated", "paid")


class CaseNotVisible(Exception):
    """Caso inexistente ou fora do acesso do usuário (RLS) → 404."""


class CaseFinalized(Exception):
    """Caso finalizado (completed/closed) ou soft-deletado → 409."""


class CasePaymentRequired(Exception):
    """Gate de pagamento (PAYMENT_GATE=hard): trabalho pago exige pagamento → 402."""


def assert_case_writable(cur, case_id) -> None:
    """Levanta ``CaseNotVisible`` (404) ou ``CaseFinalized`` (409) se o caso não
    puder receber escrita. Chamar ANTES de update/delete de recursos do caso,
    dentro da mesma ``tenant_tx``.

    Semântica alinhada ao create de case_results: inexistente OU soft-deletado
    → 404 (CaseNotVisible); completed/closed → 409 (CaseFinalized).

    ``FOR UPDATE`` trava a linha do caso até o commit da tenant_tx: uma finalização
    concorrente do mesmo caso fica bloqueada até a escrita-filha terminar, tornando
    o guard atômico (sem TOCTOU)."""
    cur.execute(
        "SELECT status FROM public.cases WHERE id = %s AND deleted_at IS NULL FOR UPDATE",
        (str(case_id),),
    )
    row = cur.fetchone()
    if row is None:
        raise CaseNotVisible()
    if row["status"] in _FINALIZED_STATUSES:
        raise CaseFinalized()


def assert_case_paid(cur, case_id) -> None:
    """Gate de pagamento: quando ``PAYMENT_GATE=hard``, o caso só libera trabalho pago
    (triagem/relatório) se o pedido estiver pago (``payment_status`` em
    ``simulated``/``paid``). Fora do modo ``hard`` é no-op — mantém o MVP local e a
    suíte de testes fluindo sem exigir pagamento. Chamar dentro da ``tenant_tx``.

    Antes de ligar ``hard`` na AWS, ver spec §8."""
    if os.getenv("PAYMENT_GATE", "soft").strip().lower() != "hard":
        return
    cur.execute("SELECT payment_status FROM public.requests WHERE case_id = %s",
                (str(case_id),))
    row = cur.fetchone()
    status = (row["payment_status"] if row else None) or "pending"
    if status not in _PAID_STATUSES:
        raise CasePaymentRequired()
