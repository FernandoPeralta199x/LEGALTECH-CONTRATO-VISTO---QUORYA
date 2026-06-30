"""Respostas HTTP padronizadas (com CORS) para os handlers Lambda."""
import json
from datetime import datetime, timezone
from uuid import uuid4

CORS_HEADERS = {
    "Content-Type": "application/json",
    # CORS amplo no MVP; restringir por ambiente no deploy (Fase 7).
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS,HEAD",
    "Access-Control-Allow-Headers": (
        "Content-Type,X-Amz-Date,Authorization,X-Api-Key,"
        "X-Amz-Security-Token,X-Amz-User-Agent,X-Amzn-Trace-Id"
    ),
    # respostas carregam token/PII -> não cachear (em proxies/navegador)
    "Cache-Control": "no-store",
}


def success_response(status_code=200, message="Sucesso", data=None):
    body = {"message": message, "timestamp": datetime.now(timezone.utc).isoformat()}
    if data is not None:
        body["data"] = data
    return {"statusCode": status_code, "headers": CORS_HEADERS, "body": json.dumps(body)}


def error_response(status_code=400, error_message="Erro na requisição"):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps({
            "error": str(error_message),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    }


def generate_uuid():
    """UUID v4 como string (id de `users`, gerado pela aplicação)."""
    return str(uuid4())
