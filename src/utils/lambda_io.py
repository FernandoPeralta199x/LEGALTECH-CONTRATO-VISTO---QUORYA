"""Helpers comuns de I/O dos handlers Lambda: parse do body, validação de UUID,
formatação de erros do Pydantic e paginação. Centralizados aqui para evitar
duplicação e para barrar, num único ponto, inputs que o Python aceita mas o
PostgreSQL rejeita (NaN/Infinity em jsonb, null byte em text) — que de outro modo
virariam 500 no INSERT.
"""
import json
import os
import uuid

from pydantic import ValidationError

from src.utils.helpers import error_response

_MAX_PAGE = 10_000  # teto de página (OFFSET caro/overflow; keyset fica p/ Fase 7)
_MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", "1000000"))  # 1 MB


def _reject_constant(token):
    # json.loads aceita NaN/Infinity/-Infinity (extensão); jsonb do Postgres não.
    raise ValueError(f"constante JSON não permitida: {token}")


def _has_null_byte(obj):
    """Detecta `\\x00` recursivamente (PostgreSQL rejeita null byte em text)."""
    if isinstance(obj, str):
        return "\x00" in obj
    if isinstance(obj, dict):
        return any(_has_null_byte(k) or _has_null_byte(v) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_has_null_byte(x) for x in obj)
    return False


def parse_json_body(event):
    """Retorna ``(dict, None)`` em sucesso ou ``(None, error_response(400))``.

    Garante objeto JSON e rejeita valores que quebrariam no banco (NaN/Infinity,
    null byte), devolvendo 400 em vez de deixar virar 500.
    """
    raw = event.get("body") or "{}"
    if len(raw) > _MAX_BODY_BYTES:  # DoS: payload grande demais
        return None, error_response(413, "Corpo da requisição excede o limite")
    try:
        data = json.loads(raw, parse_constant=_reject_constant)
        if not isinstance(data, dict):
            return None, error_response(400, "Corpo JSON deve ser um objeto")
        if _has_null_byte(data):  # PostgreSQL rejeita null byte em text
            return None, error_response(400, "Corpo JSON contém caractere nulo inválido")
    except (json.JSONDecodeError, ValueError, RecursionError):
        # RecursionError: JSON profundamente aninhado (DoS)
        return None, error_response(400, "Corpo JSON inválido")
    return data, None


def valid_uuid(value):
    """Canoniza para UUID (string) ou retorna ``None`` se inválido."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


def fmt_validation_error(err: ValidationError) -> str:
    """Mensagem curta e segura a partir de um ``ValidationError`` do Pydantic.
    Erros de validador de MODELO (``loc`` vazio) rotulam o campo como ``corpo`` em
    vez de ``?``; ``loc`` com múltiplos níveis é unido por ponto (ex.: address.state).
    """
    def _field(e) -> str:
        loc = e.get("loc") or ()
        return ".".join(str(part) for part in loc) if loc else "corpo"

    return ", ".join(f"{_field(e)}: {e['msg']}" for e in err.errors())


def parse_pagination(params, default_size=50, max_size=100):
    """Lê page/page_size com limites. Retorna ``((page, page_size, offset), None)``
    ou ``(None, error_response(400))``. Aplica teto de página para evitar OFFSET
    que estoura bigint no PostgreSQL (→ 500)."""
    params = params or {}
    try:
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", default_size))
    except (ValueError, TypeError):
        return None, error_response(400, "page/page_size inválidos")
    page = min(max(page, 1), _MAX_PAGE)
    page_size = min(max(page_size, 1), max_size)
    return (page, page_size, (page - 1) * page_size), None
