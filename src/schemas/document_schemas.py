"""Schemas de documents (Pydantic 2). Alinhados ao schema real de
`public.documents`. O upload usa URL pré-assinada: o cliente informa os metadados
e (opcionalmente) `file_size_bytes`/`file_hash`; o arquivo vai direto ao S3.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# allowlist alinhada ao frontend (inclui 'md'); 'tiff'/'doc' mantidos (mais permissivo)
FILE_TYPE_PATTERN = "^(pdf|jpg|jpeg|png|tiff|doc|docx|txt|md)$"
# S-02: teto de tamanho declarado validado no backend (não só no front). A confirmação
# real do objeto (HeadObject) fica para a fase AWS — aqui rejeitamos tamanho fora do limite.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


class DocumentUploadSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    file_name: str = Field(..., min_length=1, max_length=255)
    file_type: str = Field(..., pattern=FILE_TYPE_PATTERN)
    file_size_bytes: Optional[int] = Field(default=None, ge=0, le=MAX_UPLOAD_BYTES)
    file_hash: Optional[str] = Field(default=None, max_length=64)
    document_classification: Optional[str] = Field(default=None, max_length=50)

    @field_validator("file_name")
    @classmethod
    def _safe_basename(cls, v: str) -> str:
        # Nome de EXIBIÇÃO reduzido a um basename seguro (sem componentes de caminho).
        # Defesa em profundidade: o objeto real fica em s3_path gerado pelo servidor,
        # mas o nome cru era persistido e reaparece em Content-Disposition (be-upload-03).
        base = v.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not base or base in (".", ".."):
            raise ValueError("file_name inválido")
        return base
