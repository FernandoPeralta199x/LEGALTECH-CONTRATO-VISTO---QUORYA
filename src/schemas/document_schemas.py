"""Schemas de documents (Pydantic 2). Alinhados ao schema real de
`public.documents`. O upload usa URL pré-assinada: o cliente informa os metadados
e (opcionalmente) `file_size_bytes`/`file_hash`; o arquivo vai direto ao S3.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

FILE_TYPE_PATTERN = "^(pdf|jpg|jpeg|png|tiff|doc|docx|txt)$"


class DocumentUploadSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    file_name: str = Field(..., min_length=1, max_length=255)
    file_type: str = Field(..., pattern=FILE_TYPE_PATTERN)
    file_size_bytes: Optional[int] = Field(default=None, ge=0)
    file_hash: Optional[str] = Field(default=None, max_length=64)
    document_classification: Optional[str] = Field(default=None, max_length=50)
