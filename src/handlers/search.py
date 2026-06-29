"""Handler Lambda de busca semântica (migração FastAPI→Serverless, Fase 5).

Protegido pelo JWT Authorizer (`@require_user`). Roda em `tenant_tx` para que a
busca vetorial herde a RLS de `documents` (o usuário só busca no que pode ver).
Gera o embedding da query (serviço abstrato OpenAI/mock) e usa pgvector (cosine).
Não vaza `str(e)` nem PII.
"""
import json
import logging
import uuid

from pydantic import ValidationError

from src.schemas.search_schemas import SearchSchema
from src.services import rag
from src.services.database import tenant_tx
from src.services.embeddings import embeddings_service
from src.utils.context import require_user
from src.utils.helpers import error_response, success_response
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


def _fmt(err: ValidationError) -> str:
    return ", ".join(
        f"{(e['loc'][0] if e['loc'] else '?')}: {e['msg']}" for e in err.errors()
    )


def _valid_uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


@require_user
def search_clauses(event, context):
    user = event["user"]
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error_response(400, "Corpo JSON inválido")
    try:
        data = SearchSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    case_id = _valid_uuid(data.case_id)
    if not case_id:
        return error_response(400, "case_id inválido")

    try:
        query_embedding = embeddings_service.embed(data.query)
    except Exception as e:
        logger.error(json.dumps({"event": "SEARCH_EMBED_ERROR", "error": type(e).__name__}))
        return error_response(502, "Falha ao gerar embedding da consulta")

    try:
        with tenant_tx(user["user_id"], user["role"]) as cur:
            rows = rag.search_similar(cur, query_embedding, case_id, data.top_k)
    except Exception as e:
        logger.error(json.dumps({"event": "SEARCH_ERROR", "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro na busca")

    results = [{
        "document_id": str(r["document_id"]),
        "file_name": r["file_name"],
        "segment_text": r["segment_text"],
        "similarity": round(float(r["similarity"]), 4) if r["similarity"] is not None else None,
    } for r in rows]
    logger.info(json.dumps({"event": "SEARCH_OK", "case_id": case_id,
                            "results": len(results)}))
    return success_response(200, f"{len(results)} resultados encontrados", results)
