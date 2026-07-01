"""Núcleo de ingestão de documento (SP2) reutilizável pelo endpoint síncrono
(documents.process_document) e pelo worker assíncrono da fila (SP1).

Pipeline mock-first idempotente (limpa-e-regera): OCR -> chunking -> embeddings,
gravando document_chunks/document_embeddings. Opera no cursor de uma transação
tenant_tx (RLS por org). Levanta DocumentNotFound/OcrFailed para o chamador decidir
o mapeamento (HTTP no handler, agent_execution.failed no worker).
"""
from __future__ import annotations

from src.adapters.ocr import create_ocr_adapter
from src.services.chunking import chunk_text
from src.services.embeddings import embeddings_service
from src.services.rag import store_chunk, store_embedding


class DocumentNotFound(Exception):
    """Documento inexistente/invisível na org da transação."""


class OcrFailed(Exception):
    """OCR não retornou texto utilizável."""


_MIME = {
    "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
    "jpeg": "image/jpeg", "tiff": "image/tiff",
}


def _content_type(file_type: str) -> str:
    return _MIME.get((file_type or "").lower(), "application/octet-stream")


def ingest_document(cur, org, doc_id) -> dict:
    """Roda a ingestão SP2 do documento e devolve métricas. Idempotente (limpa-e-regera)."""
    doc_id = str(doc_id)  # store_chunk/embedding não adaptam uuid.UUID (psycopg2)
    org = str(org)
    # FE4-26-B1: não ingere documento de caso arquivado (soft-delete)
    cur.execute("SELECT id, s3_path, file_type FROM public.documents WHERE id = %s"
                " AND EXISTS (SELECT 1 FROM public.cases c WHERE c.id = public.documents.case_id"
                " AND c.deleted_at IS NULL)", (str(doc_id),))
    doc = cur.fetchone()
    if not doc:
        raise DocumentNotFound(str(doc_id))

    # idempotência: remove embeddings antes dos chunks (FK)
    cur.execute("DELETE FROM public.document_embeddings WHERE document_id = %s", (str(doc_id),))
    cur.execute("DELETE FROM public.document_chunks WHERE document_id = %s", (str(doc_id),))

    ocr = create_ocr_adapter()
    res = ocr.extract_text(doc["s3_path"] or f"local/{doc_id}",
                           _content_type(doc.get("file_type") or "pdf"))
    if not res.success or not res.data.get("text"):
        cur.execute("UPDATE public.documents SET ocr_status='failed',"
                    " extraction_status='failed' WHERE id = %s", (str(doc_id),))
        raise OcrFailed(str(doc_id))

    text = res.data["text"]
    pages = res.data.get("pages")
    chunks = chunk_text(text)
    for i, ch in enumerate(chunks):
        chunk_id = store_chunk(cur, org, doc_id, i, ch, start_page=1, end_page=pages)
        emb = embeddings_service.embed(ch)
        store_embedding(cur, org, doc_id, chunk_id, ch, emb,
                        segment_type="chunk", embedding_model=embeddings_service.model)
    cur.execute("UPDATE public.documents SET ocr_status='done', extraction_status='done'"
                " WHERE id = %s", (str(doc_id),))

    return {
        "status": "done", "pages": pages, "chunk_count": len(chunks),
        "embedding_count": len(chunks), "ocr_source": res.source,
        "embedding_model": embeddings_service.model,
    }
