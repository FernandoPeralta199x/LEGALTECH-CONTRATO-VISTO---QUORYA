"""Helpers comuns de I/O dos handlers Lambda: parse do body, validação de UUID e
formatação de erros do Pydantic. Centralizados aqui para evitar duplicação.
"""
import json
import uuid

from pydantic import ValidationError

from src.utils.helpers import error_response


def parse_json_body(event):
    """Retorna ``(dict, None)`` em sucesso ou ``(None, error_response(400))``.

    Garante que o corpo é um OBJETO JSON: um JSON válido porém não-objeto (array,
    número, string, null, bool) faria ``Schema(**body)`` levantar ``TypeError`` (não
    ``ValidationError``) → 500 não tratado. Aqui devolvemos 400 antes disso.
    """
    try:
        data = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return None, error_response(400, "Corpo JSON inválido")
    if not isinstance(data, dict):
        return None, error_response(400, "Corpo JSON deve ser um objeto")
    return data, None


def valid_uuid(value):
    """Canoniza para UUID (string) ou retorna ``None`` se inválido."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


def fmt_validation_error(err: ValidationError) -> str:
    """Mensagem curta e segura a partir de um ``ValidationError`` do Pydantic."""
    return ", ".join(
        f"{(e['loc'][0] if e['loc'] else '?')}: {e['msg']}" for e in err.errors()
    )
