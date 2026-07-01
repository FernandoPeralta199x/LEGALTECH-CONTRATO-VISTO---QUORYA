"""Invariante de ciclo de vida do caso (CVS-007).

Bloqueia ESCRITA em recursos filhos (results, parties, review de relatório)
quando o caso está finalizado (`completed`/`closed`) ou soft-deletado.
Centraliza a regra que antes existia só no create de case_results.

Usar sempre com o cursor da ``tenant_tx`` corrente (respeita a RLS por org).
"""

_FINALIZED_STATUSES = ("completed", "closed")


class CaseNotVisible(Exception):
    """Caso inexistente ou fora do acesso do usuário (RLS) → 404."""


class CaseFinalized(Exception):
    """Caso finalizado (completed/closed) ou soft-deletado → 409."""


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
