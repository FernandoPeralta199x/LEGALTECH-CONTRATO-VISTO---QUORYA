"""RAG (busca vetorial com pgvector) — funções que operam sobre um cursor já no
contexto da transação (tenant_tx), para herdar a RLS de `documents`.

Alinhado ao schema real:
- `document_embeddings(document_id, chunk_id, segment_text, embedding vector(1536),
  segment_type, embedding_model)`
- `document_chunks(document_id, chunk_index, chunk_text, start_page, end_page)`

Índices são `vector_cosine_ops` (HNSW/ivfflat) → usa-se o operador de cosseno `<=>`.
Isolamento: desde a migração 010, `document_chunks`/`document_embeddings` têm RLS
por `organization_id` (ENABLE + FORCE), e `documents` é isolada por `organization_id`
(migração 007, não mais por `uploaded_by`). As funções abaixo rodam dentro de
`tenant_tx`, que injeta `app.organization_id`; o JOIN com `documents` é defesa adicional.
"""
from src.services.embeddings import to_vector_literal


def store_chunk(cur, organization_id, document_id, chunk_index, chunk_text,
                start_page=None, end_page=None):
    """Insere um chunk de texto e devolve o id (bigint). `organization_id` é exigido
    pela RLS por organização (migração 010) e deve ser o da org do documento."""
    cur.execute(
        "INSERT INTO public.document_chunks"
        " (organization_id, document_id, chunk_index, chunk_text, start_page, end_page)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (organization_id, document_id, chunk_index, chunk_text, start_page, end_page),
    )
    return cur.fetchone()["id"]


def store_embedding(cur, organization_id, document_id, chunk_id, segment_text, embedding,
                    segment_type="clause", embedding_model="text-embedding-3-small"):
    """Insere o embedding de um segmento (colunas reais; sem ON CONFLICT).
    `organization_id` é exigido pela RLS por organização (migração 010)."""
    cur.execute(
        "INSERT INTO public.document_embeddings"
        " (organization_id, document_id, chunk_id, segment_text, embedding,"
        "  segment_type, embedding_model)"
        " VALUES (%s, %s, %s, %s, %s::vector, %s, %s)",
        (organization_id, document_id, chunk_id, segment_text, to_vector_literal(embedding),
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
