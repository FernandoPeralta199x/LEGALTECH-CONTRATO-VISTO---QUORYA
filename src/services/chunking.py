"""Chunking de texto para RAG: divide o texto em segmentos de tamanho fixo com
overlap (janela deslizante). Determinístico — mesmo texto gera os mesmos chunks
(importante para a idempotência do pipeline de ingestão).
"""
from __future__ import annotations

DEFAULT_SIZE = 1000
DEFAULT_OVERLAP = 150


def chunk_text(text: str, size: int = DEFAULT_SIZE, overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """Divide ``text`` em chunks de ~``size`` caracteres com ``overlap`` de sobreposição.

    Retorna lista de chunks não-vazios. Texto vazio -> lista vazia; texto menor que
    ``size`` -> um único chunk.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = max(size - overlap, 1)
    chunks: list[str] = []
    for start in range(0, len(text), step):
        chunk = text[start:start + size].strip()
        if chunk:
            chunks.append(chunk)
        if start + size >= len(text):
            break
    return chunks
