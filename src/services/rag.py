"""RAG (busca vetorial com pgvector) — funções que operam sobre um cursor já no
contexto da transação (tenant_tx), para herdar a RLS de `documents`.

Alinhado ao schema real:
- `document_embeddings(document_id, chunk_id, segment_text, embedding vector(1536),
  segment_type, embedding_model)`
- `document_chunks(document_id, chunk_index, chunk_text, start_page, end_page)`

Índices são `vector_cosine_ops` (HNSW/ivfflat) → usa-se o operador de cosseno `<=>`.
`document_embeddings`/`document_chunks` NÃO têm RLS; a segurança vem do JOIN com
`documents` (que tem RLS por `uploaded_by`) DENTRO de `tenant_tx`.
"""
from src.services.embeddings import to_vector_literal


def store_chunk(cur, document_id, chunk_index, chunk_text, start_page=None, end_page=None):
    """Insere um chunk de texto e devolve o id (bigint)."""
    cur.execute(
        "INSERT INTO public.document_chunks"
        " (document_id, chunk_index, chunk_text, start_page, end_page)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (document_id, chunk_index, chunk_text, start_page, end_page),
    )
    return cur.fetchone()["id"]


def store_embedding(cur, document_id, chunk_id, segment_text, embedding,
                    segment_type="clause", embedding_model="text-embedding-3-small"):
    """Insere o embedding de um segmento (colunas reais; sem ON CONFLICT)."""
    cur.execute(
        "INSERT INTO public.document_embeddings"
        " (document_id, chunk_id, segment_text, embedding, segment_type, embedding_model)"
        " VALUES (%s, %s, %s, %s::vector, %s, %s)",
        (document_id, chunk_id, segment_text, to_vector_literal(embedding),
         segment_type, embedding_model),
    )


def search_similar(cur, query_embedding, case_id, top_k=5):
    """Busca os segmentos mais similares de um case (cosine). A RLS de `documents`
    (aplicada via tenant_tx) restringe o JOIN ao que o usuário pode ver."""
    vec = to_vector_literal(query_embedding)
    cur.execute(
        "SELECT de.document_id, d.file_name, de.segment_text,"
        " 1 - (de.embedding <=> %s::vector) AS similarity"
        " FROM public.document_embeddings de"
        " JOIN public.documents d ON d.id = de.document_id"
        " WHERE d.case_id = %s"
        " ORDER BY de.embedding <=> %s::vector"
        " LIMIT %s",
        (vec, case_id, vec, top_k),
    )
    return cur.fetchall()
