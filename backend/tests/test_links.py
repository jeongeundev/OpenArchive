"""위키링크 해석이 화면에 내놓는 순서를 고정한다.

열람 범위 계약은 `test_visibility.py`가 본다. 여기서는 그 위에 얹힌 정렬을 본다 —
정렬이 흔들리면 같은 문서를 두 번 열었을 때 링크와 백링크의 차례가 달라진다.
"""

import psycopg
import pytest
from conftest import insert_test_document

from app.services.links import find_backlinks, resolve_links


@pytest.fixture
async def links_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        yield conn


async def set_created_at(conn: psycopg.AsyncConnection, document_id, moment: str) -> None:
    """생성 시각을 명시적으로 벌린다.

    같은 초에 들어간 문서는 `created_at`이 같아져 정렬이 UUID 순으로 떨어지고,
    그러면 순서 계약을 검증하는 것이 아니라 우연을 검증하게 된다. 메타데이터만
    바꾸므로 본문 트리거는 돌지 않는다.
    """
    await conn.execute(
        "UPDATE documents SET created_at = %s WHERE id = %s", (moment, document_id)
    )


async def test_resolved_links_are_ordered_by_title_not_by_position_in_the_text(
    links_conn,
):
    source_id = await insert_test_document(
        links_conn,
        title="정렬 출발",
        content="[[B 대상]]을 먼저 쓰고 [[A 대상]]을 나중에 쓴다.",
    )

    links = await resolve_links(links_conn, document_id=source_id, user_id=None)

    assert [link.title for link in links] == ["A 대상", "B 대상"]


async def test_duplicate_titles_resolve_in_creation_order(links_conn):
    source_id = await insert_test_document(
        links_conn, title="동명 출발", content="[[같은 제목]]"
    )
    older_id = await insert_test_document(
        links_conn, title="같은 제목", content="먼저 만들어진 동명 문서"
    )
    newer_id = await insert_test_document(
        links_conn, title="같은 제목", content="나중에 만들어진 동명 문서"
    )
    await set_created_at(links_conn, older_id, "2026-01-01T00:00:00Z")
    await set_created_at(links_conn, newer_id, "2026-06-01T00:00:00Z")

    links = await resolve_links(links_conn, document_id=source_id, user_id=None)

    assert [link.document_id for link in links] == [older_id, newer_id]


async def test_backlinks_are_ordered_by_source_creation(links_conn):
    target_id = await insert_test_document(
        links_conn, title="백링크 정렬 대상", content="대상 문서"
    )
    newer_id = await insert_test_document(
        links_conn, title="나중 출발", content="[[백링크 정렬 대상]]"
    )
    older_id = await insert_test_document(
        links_conn, title="먼저 출발", content="[[백링크 정렬 대상]]"
    )
    await set_created_at(links_conn, newer_id, "2026-06-01T00:00:00Z")
    await set_created_at(links_conn, older_id, "2026-01-01T00:00:00Z")

    backlinks = await find_backlinks(links_conn, document_id=target_id, user_id=None)

    assert [backlink.document_id for backlink in backlinks] == [older_id, newer_id]
    assert [backlink.title for backlink in backlinks] == ["먼저 출발", "나중 출발"]


async def test_a_document_without_wikilinks_resolves_to_an_empty_list(links_conn):
    document_id = await insert_test_document(
        links_conn, title="링크 없음", content="다른 문서를 가리키지 않는 본문"
    )

    assert await resolve_links(links_conn, document_id=document_id, user_id=None) == []
    assert await find_backlinks(links_conn, document_id=document_id, user_id=None) == []
