"""Testes PUROS (sem DB) dos schemas de caso.

ARQ-07: trava anti-drift do CASE_STATUS_PATTERN pelo LADO QUE MUDA (o backend).
O frontend mantém um espelho manual (`BACKEND_CASE_STATUS_VALUES` em
contrato_visto_frontend/src/types/domain.ts) + um teste de fronteira (contracts.test.ts),
mas os dois lados daquela igualdade vivem no repo do FE: se alguém editar
CASE_STATUS_PATTERN aqui, NADA no FE falha. Este teste fecha o buraco do lado que
de fato muda — editar o pattern quebra a suíte do backend e força reconciliação
consciente com o mirror do FE. Custo ~zero, sem runtime.
"""
import re

from src.schemas.case_schemas import CASE_STATUS_PATTERN

# Espelho literal de contrato_visto_frontend/src/types/domain.ts
# (BACKEND_CASE_STATUS_VALUES). Manter esta lista e o mirror do FE em sincronia:
# uma edição no pattern do backend deve ser reconciliada nos DOIS lugares.
EXPECTED_CASE_STATUSES = [
    "open",
    "in_progress",
    "awaiting_triage",
    "triage_completed",
    "report_ready",
    "completed",
    "closed",
]


def test_case_status_pattern_espelha_o_frontend():
    # `\w+` captura cada alternativa do pattern "^(a|b|c)$" (letras + underscore).
    got = re.findall(r"\w+", CASE_STATUS_PATTERN)
    assert got == EXPECTED_CASE_STATUSES, (
        "CASE_STATUS_PATTERN mudou. Reconcilie o mirror do frontend "
        "(domain.ts: BACKEND_CASE_STATUS_VALUES) antes de atualizar esta lista."
    )
