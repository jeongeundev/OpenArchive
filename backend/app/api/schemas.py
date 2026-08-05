from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DocumentSummary(BaseModel):
    id: UUID
    title: str
    filename: str | None
    content_type: str
    version: int
    owner_id: str
    visibility: str
    tags: list[str]
    embedding_status: str
    created_at: datetime
    updated_at: datetime


class TextVersion(BaseModel):
    version: int
    created_at: datetime


class DocumentDetail(DocumentSummary):
    content: str
    versions: list[TextVersion]
    chunk_count: int
    chunk_version: int | None
