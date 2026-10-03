"""AI 에이전트에 문서 근거를 공급하는 FastMCP stdio 서버."""

import asyncio
import functools
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from mcp.server.fastmcp import FastMCP

from openarchive.config import get_settings
from openarchive.db import close_pool, connection, get_pool, is_unavailable
from openarchive.embeddings import get_provider
from openarchive.services.documents import ExtractionStatus, create_text_document
from openarchive.services.documents import get_document as get_document_service
from openarchive.services.documents import list_documents as list_documents_service
from openarchive.services.related import find_related
from openarchive.services.search import search_documents as search_documents_service

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


class MissingUserContext(Exception):
    """MCP_USER_ID가 없어 공급 주체를 확정할 수 없는 경우."""


class DatabaseUnavailable(Exception):
    """백오프 예산 안에 DB가 돌아오지 않은 경우. 에이전트에게는 이 문구만 보인다."""


# 웹 UI와 같은 백오프다 (ADR-048 결정 4). 60초는 #110 B의 최악 중단(42초)에 여유를 둔 값.
BACKOFF_START_SECONDS = 1
BACKOFF_CAP_SECONDS = 8
BACKOFF_BUDGET_SECONDS = 60

# 테스트가 실제로 기다리지 않도록 바꿔 끼우는 자리.
_now = time.monotonic
_sleep = asyncio.sleep
_jitter = random.uniform


def with_backoff(tool: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
    """DB 일시 불가용이면 지수 백오프 + 전체 지터로 다시 부른다. **읽기와 멱등키가 있는
    쓰기에만 쓴다** — 키 없는 쓰기는 커밋 도달 여부를 알 수 없어 다시 하면 문서가 두 번
    생긴다(ADR-047).

    매 시도는 도구 본문을 통째로 다시 불러 풀에서 새 연결을 빌린다. 오류 난 연결은
    `connection()`이 이미 버렸다(ADR-048 결정 2).
    """

    @functools.wraps(tool)
    async def wrapper(*args, **kwargs) -> dict:
        deadline = _now() + BACKOFF_BUDGET_SECONDS
        attempt = 0
        while True:
            try:
                return await tool(*args, **kwargs)
            except psycopg.Error as error:
                if not is_unavailable(error):
                    raise
                delay = _jitter(0, min(BACKOFF_CAP_SECONDS, BACKOFF_START_SECONDS * 2**attempt))
                if _now() + delay > deadline:
                    raise DatabaseUnavailable(
                        "문서 저장소에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도하세요."
                    ) from error
                await _sleep(delay)
                attempt += 1

    return wrapper


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


@with_backoff
async def search_documents(
    query: str,
    tags: list[str] | None = None,
    content_type: str | None = None,
    k: int = 10,
) -> dict:
    """질의와 정형 필터에 맞는 발췌·출처·기준 버전을 반환합니다.

    AI가 답변 근거로 사용할 사내 문서 구절을 찾을 때 사용합니다.
    """
    async with connection() as conn:
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
                "preview": hit.preview,
                "chunk_index": hit.chunk_index,
                # 확장 결과의 dist는 진입점 거리 + GRAPH_DISTANCE_PENALTY라 `1 - dist`가
                # 음수이고, 같은 진입점에서 나온 확장은 전부 동점이다. 정렬용 값이지
                # 유사도가 아니므로 싣지 않는다 — 어떻게 도달했는지는 via가 말한다.
                "score": hit.score if hit.via is None else None,
                "based_on_version": hit.based_on_version,
                "via": (
                    {
                        "from_document_id": str(hit.via.from_document_id),
                        "kind": hit.via.kind,
                        "depth": hit.via.depth,
                    }
                    if hit.via is not None
                    else None
                ),
            }
            for hit in hits
        ]
    }


def _document_payload(document: dict) -> dict:
    """서비스의 `id`를 MCP 응답 규약인 `document_id`로 바꾼다."""
    payload = _json_value(document)
    payload["document_id"] = str(payload.pop("id"))
    return payload


@with_backoff
async def get_document(document_id: str) -> dict:
    """문서 텍스트·텍스트 버전 목록·청크 상태를 반환합니다.

    검색 결과의 문서 전체 내용과 색인 기준 버전을 확인할 때 사용합니다.
    """
    async with connection() as conn:
        parsed_id = UUID(document_id)
        document = await get_document_service(
            conn, parsed_id, user_id=get_settings().mcp_user_id
        )
        related = await find_related(
            conn, document_id=parsed_id, user_id=get_settings().mcp_user_id
        )
    payload = _document_payload(document)
    payload["related"] = _json_value(asdict(related))
    return payload


@with_backoff
async def list_documents(
    tag: str | None = None,
    status: str | None = None,
    extraction_status: ExtractionStatus | None = None,
) -> dict:
    """접근 가능한 문서의 메타데이터 요약 목록을 반환합니다.

    검색 전에 사용 가능한 문서를 태그나 임베딩 상태로 둘러볼 때 사용합니다.
    status는 임베딩 상태입니다. 텍스트 인식에 실패한 문서(extraction_status="failed")는
    임베딩되지 않은 채 status="pending"으로 남으므로, 곧 검색될 문서만 보려면
    extraction_status="done"을 함께 지정합니다.
    """
    async with connection() as conn:
        documents = await list_documents_service(
            conn,
            user_id=get_settings().mcp_user_id,
            tag=tag,
            embedding_status=status,
            extraction_status=extraction_status,
        )
    return {"items": [_document_payload(document) for document in documents]}


async def create_document(
    title: str,
    content: str,
    content_type: Literal["txt", "md"] = "md",
    tags: list[str] | None = None,
    visibility: Literal["public", "private"] = "public",
    grant_users: list[str] | None = None,
    grant_groups: list[str] | None = None,
) -> dict:
    """문서 텍스트를 저장하고 임베딩 파이프라인을 기동합니다.

    기본 공개범위는 public(조직 공개)입니다. private는 소유자와 부여 대상만 봅니다.
    부여 대상은 grant_users(사용자명)·grant_groups(그룹명)로 주며 visibility="private"일 때만
    줄 수 있습니다. 모르는 이름이나 소유자 자신이 있으면 문서를 만들지 않습니다. 이미 있는 문서의 열람 범위는
    이 도구로 바꿀 수 없습니다(웹 로그인 세션 전용).

    소유자는 서버의 MCP_USER_ID 환경이 정하며 인자로 지정할 수 없습니다. 임베딩은
    비동기이므로 응답의 embedding_status가 pending일 수 있습니다.
    """
    user_id = get_settings().mcp_user_id
    # 빈 값·공백도 주체가 없는 상태다. `MCP_USER_ID=""`는 None이 아니라 빈 문자열로 들어오고,
    # owner_id에는 FK도 CHECK도 없어 그대로 두면 소유자 없는 문서가 조용히 저장된다.
    if not user_id or not user_id.strip():
        raise MissingUserContext("문서를 만들려면 MCP_USER_ID 환경변수를 설정해야 합니다.")
    # 키는 도구 호출마다 하나다. 백오프 바깥에서 만들어야 재시도가 같은 키를 쓴다.
    idempotency_key = str(uuid4())

    @with_backoff
    async def attempt() -> dict:
        async with connection() as conn:
            document = await create_text_document(
                conn,
                title=title,
                content=content,
                content_type=content_type,
                owner_id=user_id,
                tags=tags,
                visibility=visibility,
                idempotency_key=idempotency_key,
                grant_users=grant_users,
                grant_groups=grant_groups,
            )
        return _document_payload(document)

    return await attempt()


mcp.tool()(search_documents)
mcp.tool()(get_document)
mcp.tool()(list_documents)
mcp.tool()(create_document)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
