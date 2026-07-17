"""Invariante de ciclo de vida do caso (CVS-007).

Bloqueia ESCRITA em recursos filhos (results, parties, review de relatório)
quando o caso está finalizado (`completed`/`closed`) ou soft-deletado.
Centraliza a regra que antes existia só no create de case_results.

Usar sempre com o cursor da ``tenant_tx`` corrente (respeita a RLS por org).
"""
import os

from src.utils.safety import is_production

_FINALIZED_STATUSES = ("completed", "closed")
# Pagamento REAL que libera trabalho pago (triagem/relatório) em produção.
_PAID_STATUSES = ("paid",)
# Em dev/test, o pagamento MOCK ('simulated', do PaymentProvider mock) também libera,
# para exercitar o fluxo pós-pagamento (gate hard) sem um gateway real. Em produção,
# uma linha 'simulated' só pode ser RESÍDUO (dados migrados de dev) — e não deve
# destravar trabalho pago que gasta APIs reais. Defesa em profundidade, mesmo espírito
# do PRC-01/A2 (o mock nunca tem efeito de dinheiro real fora de dev). O boot produtivo
# já exige provider real (enforce_production_safety), então isto é a segunda camada.
_PAID_STATUSES_DEV = ("simulated", "paid")


class CaseNotVisible(Exception):
    """Caso inexistente ou fora do acesso do usuário (RLS) → 404."""


class CaseFinalized(Exception):
    """Caso finalizado (completed/closed) ou soft-deletado → 409."""


class CasePaymentRequired(Exception):
    """Gate de pagamento (PAYMENT_GATE=hard): trabalho pago exige pagamento → 402."""


class CaseNotTriaged(Exception):
    """Relatório exige triagem executada (evidências) → 409."""


class CasesLimitReached(Exception):
    """A organização atingiu o limite de casos (pricing_configs.cases_limit) → 402."""


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
    (triagem/relatório) se o pedido estiver pago. Em PRODUÇÃO, apenas ``paid`` (dinheiro
    real); em dev/test, ``simulated``/``paid`` (o mock também libera, para testar o
    fluxo sem gateway real). Fora do modo ``hard`` é no-op — mantém o MVP local e a
    suíte de testes fluindo sem exigir pagamento. Chamar dentro da ``tenant_tx``.

    Antes de ligar ``hard`` na AWS, ver spec §8."""
    if os.getenv("PAYMENT_GATE", "soft").strip().lower() != "hard":
        return
    cur.execute("SELECT payment_status FROM public.requests WHERE case_id = %s",
                (str(case_id),))
    row = cur.fetchone()
    status = (row["payment_status"] if row else None) or "pending"
    accepted = _PAID_STATUSES if is_production() else _PAID_STATUSES_DEV
    if status not in accepted:
        raise CasePaymentRequired()


def assert_case_triaged(cur, case_id) -> None:
    """Exige que a triagem tenha SIDO EXECUTADA antes de gerar o parecer — um
    relatório não pode ser produzido sem análise/evidências (achado do pentest
    2026-07-09). Levanta ``CaseNotTriaged`` (409) se nenhum ``triage_module`` do
    caso está ``done``. Chamar dentro da ``tenant_tx``, ANTES de gerar o relatório."""
    cur.execute(
        "SELECT count(*) AS n FROM public.triage_modules"
        " WHERE case_id = %s AND status = 'done'",
        (str(case_id),),
    )
    if cur.fetchone()["n"] == 0:
        raise CaseNotTriaged()


def cases_limit_status(cur, org):
    """(cases_limit, active_count) da organização, dentro da ``tenant_tx`` (RLS por org).
    ``cases_limit`` NULL significa "sem limite"."""
    cur.execute("SELECT cases_limit FROM public.pricing_configs WHERE organization_id = %s",
                (org,))
    row = cur.fetchone()
    cases_limit = row["cases_limit"] if row else None
    cur.execute("SELECT count(*) AS n FROM public.cases WHERE deleted_at IS NULL")
    return cases_limit, cur.fetchone()["n"]


def assert_cases_within_limit(cur, org) -> None:
    """Bloqueia a criação de um novo caso quando a organização atingiu o limite
    (``pricing_configs.cases_limit``). Fail-safe: limite NULL => ilimitado. Chamar
    dentro da ``tenant_tx`` ANTES do INSERT do caso.

    É uma quota de negócio (não invariante de segurança): há uma janela TOCTOU estreita
    sob alta concorrência que pode admitir 1 caso além do limite — aceitável para o MVP
    (a RLS por org e a corretude dos dados não dependem disso)."""
    cases_limit, active = cases_limit_status(cur, org)
    if cases_limit is not None and active >= cases_limit:
        raise CasesLimitReached()