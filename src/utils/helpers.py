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


def _headers(request_id):
    """CORS + correlação (C3-03): X-Request-Id no header, exposto ao navegador via
    Access-Control-Expose-Headers (senão o JS não lê o header em CORS). Dict novo por
    resposta — NUNCA muta CORS_HEADERS (constante compartilhada)."""
    return {
        **CORS_HEADERS,
        "X-Request-Id": request_id,
        "Access-Control-Expose-Headers": "X-Request-Id",
    }


def success_response(status_code=200, message="Sucesso", data=None, request_id=None):
    # C3-03: id de correlação no envelope E no header, para o frontend parar de sintetizar
    # o seu e passar a rastrear o mesmo id da resposta (base de suporte/observabilidade).
    rid = request_id or generate_uuid()
    body = {"message": message, "request_id": rid,
            "timestamp": datetime.now(timezone.utc).isoformat()}
    if data is not None:
        body["data"] = data
    return {"statusCode": status_code, "headers": _headers(rid), "body": json.dumps(body)}


def error_response(status_code=400, error_message="Erro na requisição", request_id=None):
    rid = request_id or generate_uuid()
    return {
        "statusCode": status_code,
        "headers": _headers(rid),
        "body": json.dumps({
            "error": str(error_message),
            "request_id": rid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    }


def generate_uuid():
    """UUID v4 como string (id de `users`, gerado pela aplicação)."""
    return str(uuid4())
