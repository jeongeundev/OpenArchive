from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_conn, get_embedding_provider, optional_user_id
from app.api.schemas import SearchRequest, SearchResponse
from app.embeddings.base import EmbeddingProvider
from app.services.search import SEARCH_SQL, search_documents

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    conn: Annotated[psycopg.AsyncConnection, Depends(get_conn)],
    provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    user_id: Annotated[str | None, Depends(optional_user_id)],
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
