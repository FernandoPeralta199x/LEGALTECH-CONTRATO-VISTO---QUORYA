"""Máquina de estados da cobrança Pix (PURA, sem I/O — testável isoladamente).

O estado autoritativo é da COBRANÇA (charge), 1:1 com a request/caso, e é projetado em
`requests.payment_status`. Substitui os valores ad-hoc de hoje (pending/processing/simulated)
por um vocabulário explícito com transições validadas. Espelha o estilo puro de
`src/services/pricing/installments.py` (sem banco, sem rede)."""
from __future__ import annotations

from typing import Literal

PixState = Literal[
    "PENDING_PAYMENT", "PAID", "EXPIRED", "CANCELLED", "REFUNDED", "PAYMENT_FAILED"
]

PENDING_PAYMENT: PixState = "PENDING_PAYMENT"
PAID: PixState = "PAID"
EXPIRED: PixState = "EXPIRED"
CANCELLED: PixState = "CANCELLED"
REFUNDED: PixState = "REFUNDED"
PAYMENT_FAILED: PixState = "PAYMENT_FAILED"

ALL_STATES: frozenset[str] = frozenset(
    {PENDING_PAYMENT, PAID, EXPIRED, CANCELLED, REFUNDED, PAYMENT_FAILED}
)

# Terminais: não mudam sozinhos. EXPIRED/PAYMENT_FAILED/CANCELLED só evoluem via NOVA
# cobrança (novo paymentId), preservando o histórico da anterior. PAID só -> REFUNDED.
TERMINAL: frozenset[str] = frozenset({PAID, EXPIRED, CANCELLED, REFUNDED, PAYMENT_FAILED})

# Estados a partir dos quais uma NOVA cobrança pode ser criada (nova tentativa de pagamento).
RETRYABLE: frozenset[str] = frozenset({EXPIRED, PAYMENT_FAILED, CANCELLED})

# "Pago o suficiente" para liberar trabalho (gate de negócio). Só PAID libera de fato —
# ao contrário do mock atual, que aceita "simulated" (case_lifecycle._PAID_STATUSES).
PAID_STATES: frozenset[str] = frozenset({PAID})

# Transições IN-PLACE válidas na MESMA cobrança.
_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING_PAYMENT: frozenset({PAID, EXPIRED, PAYMENT_FAILED, CANCELLED}),
    PAID: frozenset({REFUNDED}),
    EXPIRED: frozenset(),
    PAYMENT_FAILED: frozenset(),
    CANCELLED: frozenset(),
    REFUNDED: frozenset(),
}


class InvalidPixTransition(ValueError):
    """Transição de estado de cobrança Pix não permitida."""


def is_valid_transition(current: str, target: str) -> bool:
    """True se `target` é uma transição in-place válida a partir de `current`."""
    return target in _TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    """Levanta InvalidPixTransition se a transição in-place não for permitida."""
    if not is_valid_transition(current, target):
        raise InvalidPixTransition(f"Transição de cobrança inválida: {current} -> {target}")


def is_terminal(state: str) -> bool:
    return state in TERMINAL


def can_retry(state: str) -> bool:
    """True se, a partir de `state`, uma NOVA cobrança pode ser criada (nova tentativa)."""
    return state in RETRYABLE
