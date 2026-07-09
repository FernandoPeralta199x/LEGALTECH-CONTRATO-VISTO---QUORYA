"""Tipo MIME por extensão de arquivo — fonte única.

Antes duplicado em três lugares (``cases._mime``, ``documents._content_type`` e
``document_ingestion._content_type``/``_MIME``). ``document_ingestion`` cobria só
um subconjunto (sem txt/doc/docx); a unificação usa o mapa completo — apenas mais
correto (esses tipos passam a receber o MIME certo no OCR em vez de octet-stream).
"""
from __future__ import annotations

_MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "tiff": "image/tiff",
    "txt": "text/plain",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def mime_for(file_type) -> str:
    """MIME da extensão (case-insensitive; ``None``/desconhecido → octet-stream)."""
    return _MIME_BY_EXTENSION.get(
        (file_type or "").lower(), "application/octet-stream"
    )
