"""Mapeamento único ocr_status (coluna legada) <-> status de documento V2 do frontend.

Fonte da verdade compartilhada por handlers (documents, cases) para evitar divergência:
a listagem (`list_documents`) mapeava 'done' -> 'processed', mas o agregado do caso
(`get_case_aggregate`) expunha 'done' cru, o que renderizava um badge sem rótulo no front.
"""

# ocr_status (coluna legada) -> status V2 esperado pelo frontend
DOC_STATUS_MAP = {"pending": "pending_upload", "done": "processed"}

# inverso: status V2 (filtro/PATCH do frontend) -> ocr_status legado. Conjunto fechado:
# só estes status mapeiam para a coluna ocr_status; outros são rejeitados (evita filtro
# vazio silencioso e poluição de ocr_status).
DOC_STATUS_REV = {"pending_upload": "pending", "processed": "done", "failed": "failed"}


def to_v2_status(ocr_status) -> str:
    """Converte ocr_status -> status V2 do frontend; valor desconhecido passa cru (defensivo)."""
    raw = ocr_status or "pending_upload"
    return DOC_STATUS_MAP.get(raw, raw)
