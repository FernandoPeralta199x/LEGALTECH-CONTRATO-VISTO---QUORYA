"""LGPD-01: utilitários de mascaramento de PII. Portado de
``legaltech-aws/apps/api/src/modules/common/pii.py``.

NUNCA expor PII crua (e-mail, CPF, RG, CNPJ, telefone, documento) em respostas
ou logs. Use estes helpers para mascarar antes de qualquer exposição.
"""
from __future__ import annotations

REDACTED = "<redacted>"


def mask_email(email: str | None) -> str:
    """Mascara o e-mail mantendo só a 1ª letra do local + domínio.

    ``fernando@example.com`` -> ``f*******@example.com``. Vazio/ inválido vira
    ``<redacted>`` (nunca expõe o original).
    """
    if not email or "@" not in email:
        return REDACTED
    local, _, domain = email.partition("@")
    if not local or not domain:
        return REDACTED
    masked_local = "*" if len(local) <= 1 else local[0] + "*" * (len(local) - 1)
    return f"{masked_local}@{domain}"


def mask_phone(phone: str | None) -> str:
    """Mascara telefone mantendo DDD + 2 últimos dígitos: ``(11) ****-**99``."""
    if not phone:
        return REDACTED
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 4:
        return REDACTED
    return f"({digits[:2]}) ****-**{digits[-2:]}"


def mask_document(document: str | None) -> str | None:
    """Mascara CPF/CNPJ mantendo só os 2 últimos dígitos (``None`` se vazio)."""
    if not document:
        return None
    digits = "".join(c for c in document if c.isdigit())
    if len(digits) == 11:
        return f"***.***.***-{digits[-2:]}"
    if len(digits) == 14:
        return f"**.***.***/****-{digits[-2:]}"
    return "Documento protegido"
