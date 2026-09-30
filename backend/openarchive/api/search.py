from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from openarchive.api.deps import Connection, get_embedding_provider, require_user_id
from openarchive.api.schemas import SearchRequest, SearchResponse
from openarchive.embeddings.base import EmbeddingProvider
from openarchive.services.search import SEARCH_SQL, search_documents

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    conn: Connection,
    provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> SearchResponse:
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="검색어는 비어 있을 수 없습니다.")

    items = await search_documents(
        conn,
        provider,
        query=body.query,
        user_id=user_id,
        tags=body.tags,
        content_type=body.content_type,
        k=body.k,
    )
    return SearchResponse(items=items, sql=SEARCH_SQL)
