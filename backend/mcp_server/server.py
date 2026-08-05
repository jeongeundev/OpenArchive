"""AI 에이전트에 문서 근거를 공급하는 FastMCP stdio 서버."""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.db import close_pool, get_pool
from app.embeddings import get_provider
from app.services.documents import get_document as get_document_service
from app.services.documents import list_documents as list_documents_service
from app.services.search import search_documents as search_documents_service

provider = get_provider()


@asynccontextmanager
async def lifespan(server: FastMCP):
    pool = get_pool()
    await pool.open()
    try:
        yield
    finally:
        await close_pool()


mcp = FastMCP("OpenArchive", lifespan=lifespan)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


async def search_documents(
    query: str,
    tags: list[str] | None = None,
    content_type: str | None = None,
    k: int = 10,
) -> dict:
    """질의와 정형 필터에 맞는 발췌·출처·기준 버전을 반환합니다.

    AI가 답변 근거로 사용할 사내 문서 구절을 찾을 때 사용합니다.
    """
    async with get_pool().connection() as conn:
        hits = await search_documents_service(
            conn,
            provider,
            query=query,
            user_id=get_settings().mcp_user_id,
            tags=tags,
            content_type=content_type,
            k=k,
        )
    return {
        "items": [
            {
                "document_id": str(hit.document_id),
                "title": hit.title,
                "filename": hit.filename,
                "content_type": hit.content_type,
                "tags": hit.tags,
                "excerpt": hit.content,
                "chunk_index": hit.chunk_index,
                "score": hit.score,
                "based_on_version": hit.based_on_version,
            }
            for hit in hits
        ]
    }


async def get_document(document_id: str) -> dict:
    """문서의 추출 텍스트·텍스트 버전 목록·청크 상태를 반환합니다.

    검색 결과의 문서 전체 내용과 색인 기준 버전을 확인할 때 사용합니다.
    """
    async with get_pool().connection() as conn:
        document = await get_document_service(
            conn, UUID(document_id), user_id=get_settings().mcp_user_id
        )
    result = _json_value(document)
    result["document_id"] = str(result.pop("id"))
    return result


async def list_documents(tag: str | None = None, status: str | None = None) -> dict:
    """접근 가능한 문서의 메타데이터 요약 목록을 반환합니다.

    검색 전에 사용 가능한 문서를 태그나 임베딩 상태로 둘러볼 때 사용합니다.
    """
    async with get_pool().connection() as conn:
        documents = await list_documents_service(
            conn,
            user_id=get_settings().mcp_user_id,
            tag=tag,
            embedding_status=status,
        )
    items = []
    for document in documents:
        item = _json_value(document)
        item["document_id"] = str(item.pop("id"))
        items.append(item)
    return {"items": items}


mcp.tool()(search_documents)
mcp.tool()(get_document)
mcp.tool()(list_documents)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
