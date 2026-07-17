"""Validação de documentos brasileiros (CPF/CNPJ) — fonte única compartilhada.

Server-side (não confiar no frontend). Suporta o **CNPJ alfanumérico** (RFB,
vigente a partir de 2026): as 12 posições-base podem conter ``0-9`` e ``A-Z`` e os
2 dígitos verificadores permanecem numéricos. O cálculo do DV usa o valor
``ASCII(caractere) - 48`` de cada posição-base (``'0'..'9' -> 0..9``,
``'A'..'Z' -> 17..42``), mantendo os mesmos pesos e o mesmo mód-11 do CNPJ numérico
(que é o caso particular em que todo caractere é dígito).
"""
from __future__ import annotations

import re

_PUNCT = re.compile(r"[.\-/\s]")
_CNPJ_SHAPE = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")
_CPF_SHAPE = re.compile(r"^[0-9]{11}$")

_CNPJ_PESOS_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
_CNPJ_PESOS_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]


def clean_document(raw: str) -> str:
    """Remove pontuação (``. - /`` e espaços) e uppercase. Mantém alfanuméricos
    (necessário para o CNPJ alfanumérico 2026)."""
    return _PUNCT.sub("", str(raw or "")).upper()


def _char_value(c: str) -> int:
    """Valor do caractere no cálculo do DV do CNPJ alfanumérico: ``ASCII(c) - 48``."""
    return ord(c) - 48


def is_valid_cpf(cpf: str) -> bool:
    if not _CPF_SHAPE.match(cpf) or len(set(cpf)) == 1:
        return False
    for i in (9, 10):
        soma = sum(int(cpf[n]) * ((i + 1) - n) for n in range(i))
        dv = (soma * 10) % 11
        dv = 0 if dv == 10 else dv
        if dv != int(cpf[i]):
            return False
    return True


def is_valid_cnpj(cnpj: str) -> bool:
    # 12 posições-base (alfanuméricas) + 2 dígitos verificadores (numéricos).
    if not _CNPJ_SHAPE.match(cnpj) or len(set(cnpj)) == 1:
        return False
    for pesos, i in ((_CNPJ_PESOS_1, 12), (_CNPJ_PESOS_2, 13)):
        soma = sum(_char_value(cnpj[n]) * pesos[n] for n in range(i))
        dv = soma % 11
        dv = 0 if dv < 2 else 11 - dv
        if dv != int(cnpj[i]):
            return False
    return True


def infer_document_type(cleaned: str) -> str | None:
    """Deriva o tipo pelo comprimento do documento já limpo (11=CPF, 14=CNPJ)."""
    if len(cleaned) == 11:
        return "cpf"
    if len(cleaned) == 14:
        return "cnpj"
    return None


def validate_document(raw: str, document_type: str | None = None) -> tuple[str, str]:
    """Limpa e valida um documento BR (CPF/CNPJ) server-side.

    Retorna ``(cleaned, document_type)``. Levanta ``ValueError`` (mensagem sem PID)
    se o tamanho for inválido, o tipo declarado não bater com o inferido, ou o
    dígito verificador for inválido."""
    cleaned = clean_document(raw)
    inferred = infer_document_type(cleaned)
    if inferred is None:
        raise ValueError("documento deve ter 11 (CPF) ou 14 (CNPJ) caracteres")
    dtype = (document_type or inferred).lower()
    if dtype != inferred:
        raise ValueError(f"documento incompatível com document_type={document_type}")
    ok = is_valid_cpf(cleaned) if dtype == "cpf" else is_valid_cnpj(cleaned)
    if not ok:
        raise ValueError("documento inválido (dígito verificador)")
    return cleaned, dtype
