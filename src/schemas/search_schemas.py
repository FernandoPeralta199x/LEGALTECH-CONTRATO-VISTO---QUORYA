"""Schema de busca semântica (Pydantic 2)."""
from pydantic import BaseModel, ConfigDict, Field


class SearchSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    query: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
