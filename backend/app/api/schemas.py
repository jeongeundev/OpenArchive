from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.services.search import MAX_K


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


class EditDocumentRequest(BaseModel):
    content: str
    version: int


class EditDocumentResponse(DocumentSummary):
    content: str


class UpdateTagsRequest(BaseModel):
    tags: list[str]


class SearchRequest(BaseModel):
    query: str
    tags: list[str] | None = None
    content_type: str | None = None
    k: int = Field(default=10, ge=1, le=MAX_K)


class SearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    title: str
    filename: str | None
    tags: list[str]
    content_type: str
    chunk_index: int
    content: str
    score: float
    based_on_version: int


class SearchResponse(BaseModel):
    items: list[SearchResult]
    sql: str


class RelatedDocumentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    title: str
    tags: list[str]
    kind: str
    score: float


class IdenticalDocumentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    title: str


class RelatedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[RelatedDocumentItem]
    identical: list[IdenticalDocumentItem]
    based_on_version: int | None
    reason: str | None


class TagSuggestionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tag: str
    freq: int


class TagSuggestionsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[TagSuggestionItem]
    based_on_version: int | None
    reason: str | None
