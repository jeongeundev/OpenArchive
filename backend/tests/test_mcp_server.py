from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from app.config import get_settings
from app.db import close_pool, get_pool
from app.embeddings import FakeProvider
from app.main import app
from app.services.documents import InvalidVisibility
from app.services.parsing import UnsupportedFileType


@pytest.fixture
async def rest_client(monkeypatch, migrated_db: str):
    """MCP 서버와 **같은 이벤트 루프·같은 풀**에서 REST API를 호출하는 클라이언트.

    동기 TestClient는 실행 중인 루프 안에서 부르면 막힌다. 두 경로를 한 테스트에서
    비교하려면 ASGI 전송으로 앱 lifespan을 그대로 태워야 한다.
    """
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.delenv("MCP_USER_ID", raising=False)
    get_settings.cache_clear()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        yield client


@pytest.fixture
async def mcp_database(monkeypatch, migrated_db: str):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.delenv("MCP_USER_ID", raising=False)
    get_settings.cache_clear()
    pool = get_pool()
    await pool.open()
    try:
        yield migrated_db
    finally:
        await close_pool()


async def _seed_documents(dsn: str):
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        public_id = await insert_test_document(
            conn,
            title="공개 근거",
            content="OpenSQL 공개 정합성 근거",
            tags=["공개"],
        )
        await conn.execute(
            "UPDATE documents SET filename = %s WHERE id = %s", ("public.md", public_id)
        )
        private_id = await insert_test_document(
            conn,
            title="비공개 근거",
            content="OpenSQL 비공개 정합성 근거",
            owner_id="alice",
            visibility="private",
            tags=["비공개"],
        )
        await process_all_embedding_jobs(conn, FakeProvider())
    return public_id, private_id


async def _document_count_by_title(dsn: str, title: str) -> int:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) FROM documents WHERE title = %s", (title,)
            )
        ).fetchone()
    return row[0]


async def test_registers_exactly_four_document_tools():
    from mcp_server.server import mcp

    tools = await mcp.list_tools()

    assert {tool.name for tool in tools} == {
        "search_documents",
        "get_document",
        "list_documents",
        "create_document",
    }


# 미설정뿐 아니라 빈 값·공백도 주체가 없는 상태다. pydantic은 `MCP_USER_ID=""`를 None이 아니라
# 빈 문자열로 담고, owner_id에는 FK도 CHECK도 없어 그대로 두면 소유자 없는 문서가 조용히 생긴다.
@pytest.mark.parametrize("env_value", [None, "", "   "])
async def test_create_requires_user_context_without_changing_anonymous_reads(
    monkeypatch, mcp_database, env_value
):
    from mcp_server.server import (
        MissingUserContext,
        create_document,
        list_documents,
        search_documents,
    )

    public_id, _ = await _seed_documents(mcp_database)
    if env_value is None:
        monkeypatch.delenv("MCP_USER_ID", raising=False)
    else:
        monkeypatch.setenv("MCP_USER_ID", env_value)
    get_settings.cache_clear()

    with pytest.raises(MissingUserContext, match="MCP_USER_ID"):
        await create_document("주체 없는 문서", "저장되면 안 되는 텍스트")

    assert {item["document_id"] for item in (await list_documents())["items"]} == {
        str(public_id)
    }
    assert {
        item["document_id"]
        for item in (await search_documents("OpenSQL 공개 정합성"))["items"]
    } == {str(public_id)}
    assert await _document_count_by_title(mcp_database, "주체 없는 문서") == 0


async def test_create_uses_mcp_owner_and_starts_all_database_derivatives(
    monkeypatch, mcp_database
):
    from mcp_server.server import create_document

    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()
    async with await psycopg.AsyncConnection.connect(
        mcp_database, autocommit=True
    ) as conn:
        await insert_test_document(conn, title="Target", content="target reference")
        await process_all_embedding_jobs(conn, FakeProvider())

    created = await create_document(
        "MCP 공급 문서",
        "shared pipeline text [[Target]]",
        content_type="txt",
        tags=[" mcp ", "mcp"],
        visibility="private",
    )
    document_id = created["document_id"]

    assert created["owner_id"] == "alice"
    assert created["visibility"] == "private"
    assert created["tags"] == ["mcp"]
    assert created["embedding_status"] == "pending"

    async with await psycopg.AsyncConnection.connect(
        mcp_database, autocommit=True
    ) as conn:
        assert await process_all_embedding_jobs(conn, FakeProvider()) == 1
        row = await (
            await conn.execute(
                """
                SELECT d.owner_id,
                       (SELECT count(*) FROM embedding_jobs WHERE document_id = d.id),
                       (SELECT count(*) FROM document_versions WHERE document_id = d.id),
                       (SELECT count(*) FROM document_chunks WHERE document_id = d.id),
                       (SELECT array_agg(target_title ORDER BY target_title)
                          FROM document_links WHERE src_document_id = d.id),
                       (SELECT count(*) FROM document_edges WHERE src_document_id = d.id)
                  FROM documents d WHERE d.id = %s
                """,
                (document_id,),
            )
        ).fetchone()

    owner, jobs, versions, chunks, links, edges = row
    assert owner == "alice"
    assert (jobs, versions, links) == (1, 1, ["Target"])
    assert chunks > 0
    assert edges > 0


async def test_private_created_document_is_hidden_from_other_mcp_users(
    monkeypatch, mcp_database
):
    from mcp_server.server import create_document, list_documents, search_documents

    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()
    created = await create_document(
        "Alice private", "비공개 MCP 검색용 고유 문구", visibility="private"
    )
    async with await psycopg.AsyncConnection.connect(
        mcp_database, autocommit=True
    ) as conn:
        await process_all_embedding_jobs(conn, FakeProvider())

    monkeypatch.setenv("MCP_USER_ID", "bob")
    get_settings.cache_clear()
    listed = await list_documents()
    searched = await search_documents("비공개 MCP 검색용 고유 문구")

    assert created["document_id"] not in {
        item["document_id"] for item in listed["items"]
    }
    assert created["document_id"] not in {
        item["document_id"] for item in searched["items"]
    }


@pytest.mark.parametrize(
    ("field", "value", "expected_exception"),
    [
        ("visibility", "organization", InvalidVisibility),
        ("content_type", "pdf", UnsupportedFileType),
    ],
)
async def test_create_propagates_core_validation_without_saving_document(
    monkeypatch, mcp_database, field, value, expected_exception
):
    from mcp_server.server import create_document

    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()
    kwargs = {field: value}

    with pytest.raises(expected_exception):
        await create_document("거부 대상", "저장되면 안 되는 텍스트", **kwargs)

    assert await _document_count_by_title(mcp_database, "거부 대상") == 0


async def _login(client, dsn: str, username: str, password: str = "test-password") -> None:
    """비동기 REST 클라이언트에 실제 쿠키 세션을 만든다."""
    from app.services.auth import hash_password

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s)"
            " ON CONFLICT (username) DO NOTHING",
            (username, hash_password(password)),
        )
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200


async def test_search_tool_matches_the_rest_endpoint_and_returns_evidence(
    monkeypatch, rest_client, migrated_db: str
):
    """이슈 #9 완료 조건: 같은 질의에 REST와 MCP가 같은 결과를 준다.

    서비스 함수를 양쪽에서 부르면 동어반복이다 — 두 경로가 실제로 노출하는 응답을 본다.
    MCP는 HTTP를 타지 않아 로그인과 무관하지만(ADR-028) REST는 이제 로그인을 요구한다.
    같은 결과를 비교하려면 양쪽 시선을 같은 계정으로 맞춰야 한다.
    """
    from mcp_server.server import search_documents

    await _seed_documents(migrated_db)
    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()
    await _login(rest_client, migrated_db, "alice")

    tool_items = (await search_documents("OpenSQL 공개 정합성"))["items"]
    response = await rest_client.post("/api/search", json={"query": "OpenSQL 공개 정합성"})
    rest_items = response.json()["items"]

    assert tool_items
    assert [item["document_id"] for item in tool_items] == [
        item["document_id"] for item in rest_items
    ]
    assert [item["excerpt"] for item in tool_items] == [
        item["content"] for item in rest_items
    ]
    for field in ("title", "filename", "content_type", "tags", "based_on_version"):
        assert [item[field] for item in tool_items] == [
            item[field] for item in rest_items
        ], field
    assert tool_items[0]["based_on_version"] == 1


async def test_mcp_user_setting_controls_private_access_for_all_tools(
    monkeypatch, mcp_database
):
    from app.services.documents import DocumentNotFound
    from mcp_server.server import get_document, list_documents, search_documents

    public_id, private_id = await _seed_documents(mcp_database)

    anonymous_search = await search_documents("OpenSQL 정합성")
    anonymous_list = await list_documents()
    with pytest.raises(DocumentNotFound):
        await get_document(str(private_id))

    assert {item["document_id"] for item in anonymous_search["items"]} == {str(public_id)}
    assert {item["document_id"] for item in anonymous_list["items"]} == {str(public_id)}

    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()

    owner_search = await search_documents("OpenSQL 정합성")
    owner_list = await list_documents()
    owner_detail = await get_document(str(private_id))

    assert str(private_id) in {item["document_id"] for item in owner_search["items"]}
    assert str(private_id) in {item["document_id"] for item in owner_list["items"]}
    assert owner_detail["document_id"] == str(private_id)
    assert owner_detail["content"] == "OpenSQL 비공개 정합성 근거"
    assert owner_detail["versions"]
    assert owner_detail["chunk_count"] == 1
    assert owner_detail["chunk_version"] == 1


async def test_expanded_search_hits_carry_no_similarity_score(mcp_database):
    """확장 결과의 dist는 진입점 거리 + GRAPH_DISTANCE_PENALTY라 `1 - dist`가 음수다.

    같은 진입점에서 나온 확장은 전부 동점이기도 해서 이 값은 유사도가 아니다. 화면과
    같은 결정을 MCP에도 적용해 확장 결과에는 score를 싣지 않고 via만 남긴다. 직접
    매칭의 score는 그대로다.
    """
    from mcp_server.server import search_documents

    async with await psycopg.AsyncConnection.connect(
        mcp_database, autocommit=True
    ) as conn:
        await insert_test_document(
            conn, title="직접 진입점", content="정합성 직접 일치 문장 " * 900
        )
        await insert_test_document(
            conn, title="관계로만 도달", content="질의 어휘가 전혀 없는 별도 문서"
        )
        await process_all_embedding_jobs(conn, FakeProvider())

    items = (await search_documents("정합성 직접 일치 문장", k=2))["items"]

    direct = [item for item in items if item["via"] is None]
    expanded = [item for item in items if item["via"] is not None]
    assert direct and expanded
    assert all(item["score"] is None for item in expanded)
    assert all(isinstance(item["score"], float) for item in direct)


async def test_get_document_rejects_missing_document(mcp_database):
    from app.services.documents import DocumentNotFound
    from mcp_server.server import get_document

    with pytest.raises(DocumentNotFound):
        await get_document(str(uuid4()))


async def test_get_document_returns_related_kind(monkeypatch, mcp_database):
    from mcp_server.server import get_document

    public_id, _ = await _seed_documents(mcp_database)
    # _seed_documents는 문서 2건만 만들어 관련 문서가 1건뿐이다. 그러면 아래 정렬
    # 단언이 원소 하나라 항상 참이 된다. 점수가 다른 이웃을 하나 더 넣어 물게 한다.
    async with await psycopg.AsyncConnection.connect(
        mcp_database, autocommit=True
    ) as conn:
        await insert_test_document(
            conn, title="먼 근거", content="휴가 식대 복지 안내", tags=["무관"]
        )
        await process_all_embedding_jobs(conn, FakeProvider())

    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()
    document = await get_document(str(public_id))

    items = document["related"]["items"]
    assert len(items) >= 2
    # kind 값이 CHECK 제약이 보장하는 집합에 드는지는 항상 참이라 아무것도 검증하지
    # 않는다. kind별 묶음과 그 안의 점수 내림차순이 MCP 직렬화까지 살아 오는지를
    # 단언한다 (ADR-029 — score의 척도가 kind마다 달라 전체 정렬은 성립하지 않는다).
    kinds = [item["kind"] for item in items]
    assert kinds == sorted(kinds, key=kinds.index)
    for kind in set(kinds):
        scores = [item["score"] for item in items if item["kind"] == kind]
        assert scores == sorted(scores, reverse=True)
