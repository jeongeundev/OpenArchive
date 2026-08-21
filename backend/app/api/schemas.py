from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.services.auth import SCOPE_READ, TokenScope
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


class TextVersionDetail(TextVersion):
    content: str


class RestoreVersionRequest(BaseModel):
    # 되돌릴 과거 버전은 경로에 있다. 이 값은 호출자가 읽어온 **현재** 버전이며,
    # 서버의 현재 버전과 다르면 409다 (ADR-037 결정 3).
    current_version: int


class DocumentDetail(DocumentSummary):
    content: str
    versions: list[TextVersion]
    chunk_count: int
    chunk_version: int | None


class CreateTextDocumentRequest(BaseModel):
    title: str
    content: str
    content_type: Literal["txt", "md"] = "md"
    tags: list[str] | None = None
    visibility: Literal["public", "private"] = "public"


class EditDocumentRequest(BaseModel):
    content: str
    version: int


class EditDocumentResponse(DocumentSummary):
    content: str


class UpdateTagsRequest(BaseModel):
    tags: list[str]


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthStatus(BaseModel):
    authenticated: bool
    username: str | None
    is_admin: bool


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1)


class CreateTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: TokenScope = SCOPE_READ


class TokenSummary(BaseModel):
    id: UUID
    name: str
    scope: TokenScope
    created_at: datetime


class TokenCreated(TokenSummary):
    token: str


class JobCounts(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pending: int
    processing: int
    recovery_pending: int
    error: int


class SystemStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_address: str | None
    node_port: int
    jobs: JobCounts
    zombie_timeout_minutes: int
    last_job_finished_at: datetime | None
    inconsistent_documents: int
    embedding_provider: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class UserSummary(BaseModel):
    id: UUID
    username: str
    is_admin: bool
    created_at: datetime


class SearchRequest(BaseModel):
    query: str
    tags: list[str] | None = None
    content_type: str | None = None
    k: int = Field(default=10, ge=1, le=MAX_K)


class SearchVia(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_document_id: UUID
    kind: str
    depth: int


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
    via: SearchVia | None


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


class ResolvedLinkItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    document_id: UUID | None


class BacklinkItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    title: str


class DiagnosticDocumentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    title: str


class DiagnosticDocumentList(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    count: int
    items: list[DiagnosticDocumentItem]


class BrokenLinkItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: DiagnosticDocumentItem
    target_title: str


class BrokenLinkList(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    count: int
    items: list[BrokenLinkItem]


class DuplicatePairItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    first: DiagnosticDocumentItem
    second: DiagnosticDocumentItem
    score: float | None


class DuplicateList(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    count: int
    items: list[DuplicatePairItem]


class DuplicateDiagnostics(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    identical: DuplicateList
    overlaps: DuplicateList


class DiagnosticsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    orphans: DiagnosticDocumentList
    duplicates: DuplicateDiagnostics
    uncategorized: DiagnosticDocumentList
    broken_links: BrokenLinkList


class ClusterDocumentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    title: str


class ClusterItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    size: int
    documents: list[ClusterDocumentItem]


class ClusterConnectionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    target: str
    count: int


class ClustersResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    clusters: list[ClusterItem]
    connections: list[ClusterConnectionItem]
