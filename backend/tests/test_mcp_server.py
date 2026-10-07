from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs, seed_extraction_states

from openarchive.config import get_settings
from openarchive.db import close_pool, get_pool
from openarchive.embeddings import FakeProvider
from openarchive.main import app
from openarchive.services.documents import GrantsOnPublicDocument, InvalidVisibility
from openarchive.services.grants import UnknownGrantee, add_member, create_group
from openarchive.services.parsing import UnsupportedFileType


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
    from openarchive.mcp_server.server import mcp

    tools = await mcp.list_tools()

    assert {tool.name for tool in tools} == {
        "search_documents",
        "get_document",
        "list_documents",
        "create_document",
    }


async def test_create_records_mcp_actor(monkeypatch, mcp_database):
    from test_audit import rows

    from openarchive.mcp_server.server import create_document

    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()

    created = await create_document("MCP 감사 문서", "감사 대상 텍스트")

    assert rows(mcp_database, created["document_id"]) == [
        ("document_created", "alice", "mcp", "MCP 감사 문서", {})
    ]


@pytest.mark.parametrize("env_value", [None, ""])
async def test_create_without_user_context_leaves_no_audit(
    monkeypatch, mcp_database, env_value
):
    from test_audit import rows

    from openarchive.mcp_server.server import MissingUserContext, create_document

    if env_value is None:
        monkeypatch.delenv("MCP_USER_ID", raising=False)
    else:
        monkeypatch.setenv("MCP_USER_ID", env_value)
    get_settings.cache_clear()

    assert rows(mcp_database) == []
    with pytest.raises(MissingUserContext, match="MCP_USER_ID"):
        await create_document("주체 없는 감사 문서", "저장되면 안 되는 텍스트")
    assert rows(mcp_database) == []


# 미설정뿐 아니라 빈 값·공백도 주체가 없는 상태다. pydantic은 `MCP_USER_ID=""`를 None이 아니라
# 빈 문자열로 담고, owner_id에는 FK도 CHECK도 없어 그대로 두면 소유자 없는 문서가 조용히 생긴다.
@pytest.mark.parametrize("env_value", [None, "", "   "])
async def test_create_requires_user_context_without_changing_anonymous_reads(
    monkeypatch, mcp_database, env_value
):
    from openarchive.mcp_server.server import (
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
    from openarchive.mcp_server.server import create_document

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
        assert await process_all_embedding_jobs(conn, FakeProvider()) == 2  # 임베딩 + 관계
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
    assert (jobs, versions, links) == (2, 1, ["Target"])  # 잡 2건 = 임베딩 + 관계 (017)
    assert chunks > 0
    assert edges > 0


async def test_private_created_document_is_hidden_from_other_mcp_users(
    monkeypatch, mcp_database
):
    from openarchive.mcp_server.server import create_document, list_documents, search_documents

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
    from openarchive.mcp_server.server import create_document

    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()
    kwargs = {field: value}

    with pytest.raises(expected_exception):
        await create_document("거부 대상", "저장되면 안 되는 텍스트", **kwargs)

    assert await _document_count_by_title(mcp_database, "거부 대상") == 0


async def _login(client, dsn: str, username: str, password: str = "test-password") -> None:
    """비동기 REST 클라이언트에 실제 쿠키 세션을 만든다."""
    from openarchive.services.auth import hash_password

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
    from openarchive.mcp_server.server import search_documents

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
    for field in ("title", "filename", "content_type", "tags", "based_on_version", "passages"):
        assert [item[field] for item in tool_items] == [
            item[field] for item in rest_items
        ], field
    assert tool_items[0]["based_on_version"] == 1
    assert tool_items[0]["passages"]


async def test_mcp_user_setting_controls_private_access_for_all_tools(
    monkeypatch, mcp_database
):
    from openarchive.mcp_server.server import get_document, list_documents, search_documents
    from openarchive.services.documents import DocumentNotFound

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
    from openarchive.mcp_server.server import search_documents

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
    from openarchive.mcp_server.server import get_document
    from openarchive.services.documents import DocumentNotFound

    with pytest.raises(DocumentNotFound):
        await get_document(str(uuid4()))


async def test_get_document_returns_related_kind(monkeypatch, mcp_database):
    from openarchive.mcp_server.server import get_document

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


async def test_get_document_lists_original_files_without_bytes(mcp_database):
    """상세의 원본 판 목록은 메타데이터만 싣는다 — 바이트가 MCP 응답에 섞이지 않는다."""
    from openarchive.mcp_server.server import get_document

    public_id, _ = await _seed_documents(mcp_database)
    async with await psycopg.AsyncConnection.connect(
        mcp_database, autocommit=True
    ) as conn:
        await conn.execute(
            """
            INSERT INTO document_files
                (document_id, file_version, filename, data, text_version, uploaded_by)
            VALUES (%s, 1, 'public.md', %b, 1, 'alice')
            """,
            (public_id, b"original bytes"),
        )

    document = await get_document(str(public_id))

    assert [set(item) for item in document["files"]] == [
        {
            "file_version",
            "filename",
            "size",
            "sha256",
            "text_version",
            "uploaded_by",
            "uploaded_at",
        }
    ]
    assert document["files"][0]["size"] == len(b"original bytes")
    assert isinstance(document["files"][0]["uploaded_at"], str)


async def test_a_tool_call_that_ends_in_a_db_error_does_not_return_its_connection(
    monkeypatch, mcp_database
):
    """MCP 도구 호출도 DB 오류로 끝나면 그 연결을 닫아 풀이 버리게 한다 (ADR-048 결정 2).

    API와 같은 풀 규칙이다 — 오염된 연결이 풀로 돌아가면 이후 도구 호출마다
    `transaction()`이 `AssertionError`를 낸다(#110 B-2).
    """
    from openarchive.mcp_server import server

    borrowed = []

    async def failing_list(conn, **_):
        borrowed.append(conn)
        await conn.execute("SELECT 1/0")

    monkeypatch.setattr(server, "list_documents_service", failing_list)

    with pytest.raises(psycopg.errors.DivisionByZero):
        await server.list_documents()

    assert len(borrowed) == 1
    assert borrowed[0].closed


# ── 일시 불가용 백오프 (ADR-048 결정 4) ─────────────────────────────────────────


class FakeClock:
    """실제로 기다리지 않고 흘러간 시간만 센다."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    from openarchive.mcp_server import server

    fake = FakeClock()
    monkeypatch.setattr(server, "_now", fake.monotonic)
    monkeypatch.setattr(server, "_sleep", fake.sleep)
    # 전체 지터의 상한을 그대로 돌려줘 대기 간격이 결정적이 되게 한다.
    monkeypatch.setattr(server, "_jitter", lambda low, high: high)
    return fake


ALL_SERVERS_DOWN = psycopg.errors.lookup("58000")(
    "could not get connection from the pool - AllServersDown"
)


def _failing_then(real, failures: int, borrowed: list):
    async def call(conn, *args, **kwargs):
        borrowed.append(conn)
        if len(borrowed) <= failures:
            raise ALL_SERVERS_DOWN
        return await real(conn, *args, **kwargs)

    return call


async def test_read_tools_wait_out_an_outage(monkeypatch, mcp_database, clock):
    """#110 B의 쓰기 중단은 7~42초였다. 읽기 도구는 그동안 기다렸다 성공해야 한다."""
    from openarchive.mcp_server import server

    await _seed_documents(mcp_database)
    borrowed: list = []
    monkeypatch.setattr(
        server,
        "list_documents_service",
        _failing_then(server.list_documents_service, failures=3, borrowed=borrowed),
    )

    result = await server.list_documents()

    assert result["items"]
    assert len(borrowed) == 4
    # 오류 난 연결은 버려지고 매번 새 연결을 빌린다 (ADR-048 결정 2).
    assert all(conn.closed for conn in borrowed[:3])
    # 1초에서 시작해 두 배씩 — 전체 지터의 상한.
    assert clock.sleeps == [1, 2, 4]


async def test_backoff_is_capped_and_gives_up_after_a_minute(monkeypatch, mcp_database, clock):
    from openarchive.mcp_server import server

    borrowed: list = []
    monkeypatch.setattr(
        server,
        "search_documents_service",
        _failing_then(server.search_documents_service, failures=10_000, borrowed=borrowed),
    )

    with pytest.raises(server.DatabaseUnavailable) as caught:
        await server.search_documents("정합성")

    assert max(clock.sleeps) == 8
    assert clock.sleeps[:5] == [1, 2, 4, 8, 8]
    assert clock.now <= 60
    # 다음 대기가 60초를 넘기면 거기서 멈춘다 — 한 번 더 기다릴 수 있었으면 기다렸다.
    assert clock.now + 8 > 60
    assert "잠시 후" in str(caught.value)
    assert isinstance(caught.value.__cause__, psycopg.errors.SystemError)


async def test_get_document_also_waits_out_an_outage(monkeypatch, mcp_database, clock):
    from openarchive.mcp_server import server

    ids = await _seed_documents(mcp_database)
    borrowed: list = []
    monkeypatch.setattr(
        server,
        "find_related",
        _failing_then(server.find_related, failures=1, borrowed=borrowed),
    )

    document = await server.get_document(str(ids[0]))

    assert document["document_id"]
    assert clock.sleeps == [1]


async def test_errors_that_do_not_pass_with_time_are_not_retried(monkeypatch, mcp_database, clock):
    from openarchive.mcp_server import server

    async def failing_list(conn, **_):
        await conn.execute("SELECT 1/0")

    monkeypatch.setattr(server, "list_documents_service", failing_list)

    with pytest.raises(psycopg.errors.DivisionByZero):
        await server.list_documents()

    assert clock.sleeps == []


async def test_create_document_retries_an_ambiguous_commit_with_one_key_per_call(
    monkeypatch, mcp_database, clock
):
    """커밋은 됐는데 응답을 잃은 경우(#110 B-6)를 재현한다 — 첫 시도가 문서를 만든 뒤
    AllServersDown을 낸다. 같은 도구 호출 안의 재시도는 같은 키를 써서 처음 문서를 돌려받고,
    다음 도구 호출은 새 키로 새 문서를 만든다 (ADR-047, ADR-048 결정 4)."""
    from openarchive.mcp_server import server

    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()
    real_create = server.create_text_document
    keys: list[str] = []

    async def commit_then_lose_the_response(conn, **kwargs):
        keys.append(kwargs["idempotency_key"])
        document = await real_create(conn, **kwargs)
        if len(keys) == 1:
            await conn.commit()
            raise ALL_SERVERS_DOWN
        return document

    monkeypatch.setattr(server, "create_text_document", commit_then_lose_the_response)

    first = await server.create_document(title="t", content="c")
    second = await server.create_document(title="t", content="c")

    assert clock.sleeps == [1]
    assert keys[0] == keys[1] != keys[2]
    assert first["document_id"] != second["document_id"]
    async with await psycopg.AsyncConnection.connect(mcp_database) as conn:
        count = await (await conn.execute("SELECT count(*) FROM documents")).fetchone()
    assert count == (2,)


async def test_create_document_gives_up_after_the_backoff_budget(
    monkeypatch, mcp_database, clock
):
    from openarchive.mcp_server import server

    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()

    async def always_down(conn, **_):
        raise ALL_SERVERS_DOWN

    monkeypatch.setattr(server, "create_text_document", always_down)

    with pytest.raises(server.DatabaseUnavailable):
        await server.create_document(title="t", content="c")

    assert clock.now <= 60


async def test_wrapped_read_tools_keep_their_argument_schema():
    """백오프로 감싸도 에이전트가 보는 도구 인자는 그대로여야 한다."""
    from openarchive.mcp_server.server import mcp

    tools = {tool.name: tool for tool in await mcp.list_tools()}

    assert set(tools["search_documents"].inputSchema["properties"]) == {
        "query", "tags", "content_type", "k"
    }
    assert tools["search_documents"].inputSchema["required"] == ["query"]
    assert set(tools["get_document"].inputSchema["properties"]) == {"document_id"}
    assert set(tools["list_documents"].inputSchema["properties"]) == {
        "tag", "status", "extraction_status"
    }
    assert tools["list_documents"].inputSchema["properties"]["extraction_status"]["anyOf"][0][
        "enum"
    ] == ["pending", "done", "failed"]
    assert "사내 문서 구절" in tools["search_documents"].description
    # 멱등키는 서버가 호출마다 만든다 — 에이전트가 고르는 인자가 아니다.
    assert set(tools["create_document"].inputSchema["properties"]) == {
        "title", "content", "content_type", "tags", "visibility", "grant_users", "grant_groups"
    }


async def test_list_documents_filters_by_extraction_status(mcp_database):
    """#139 — 인식 실패 문서를 '임베딩 대기'와 구분해 고를 수 있어야 한다."""
    from openarchive.mcp_server.server import list_documents

    async with await psycopg.AsyncConnection.connect(mcp_database, autocommit=True) as conn:
        ids = await seed_extraction_states(conn)

    failed = await list_documents(extraction_status="failed")
    assert [item["document_id"] for item in failed["items"]] == [str(ids["failed"])]
    will_embed = await list_documents(status="pending", extraction_status="done")
    assert [item["document_id"] for item in will_embed["items"]] == [str(ids["done"])]


async def _seed_grantees(dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        for username in ["alice", "bob", "carol", "dave"]:
            await conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, 'unused')", (username,)
            )
        group = await create_group(conn, "인사팀")
        await add_member(conn, group["id"], "dave")


async def _grant_count(dsn: str, document_id: str) -> int:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) FROM document_grants WHERE document_id = %s", (document_id,)
            )
        ).fetchone()
    return row[0]


async def _visible_ids(monkeypatch, user: str) -> set[str]:
    from openarchive.mcp_server.server import list_documents

    monkeypatch.setenv("MCP_USER_ID", user)
    get_settings.cache_clear()
    return {item["document_id"] for item in (await list_documents())["items"]}


async def test_create_with_grantees_opens_the_document_to_them_only(monkeypatch, mcp_database):
    """ADR-044 관리 경로 — 생성 시 부여 대상은 문서와 같은 트랜잭션에 들어간다."""
    from openarchive.mcp_server.server import create_document

    await _seed_grantees(mcp_database)
    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()
    created = await create_document(
        "부여 문서",
        "부여 대상만 보는 텍스트",
        visibility="private",
        grant_users=["bob"],
        grant_groups=["인사팀"],
    )
    document_id = created["document_id"]

    assert await _grant_count(mcp_database, document_id) == 2
    assert document_id in await _visible_ids(monkeypatch, "bob")
    assert document_id in await _visible_ids(monkeypatch, "dave")  # 인사팀 구성원
    assert document_id not in await _visible_ids(monkeypatch, "carol")


@pytest.mark.parametrize(
    ("kwargs", "expected_exception", "message"),
    [
        (
            {"visibility": "public", "grant_users": ["bob"]},
            GrantsOnPublicDocument,
            "visibility=private",
        ),
        (
            {"visibility": "private", "grant_users": ["bob", "nobody"]},
            UnknownGrantee,
            "nobody",
        ),
        (
            {"visibility": "private", "grant_groups": ["없는팀"]},
            UnknownGrantee,
            "없는팀",
        ),
    ],
)
async def test_create_rejects_invalid_grantees_without_saving(
    monkeypatch, mcp_database, kwargs, expected_exception, message
):
    from openarchive.mcp_server.server import create_document

    await _seed_grantees(mcp_database)
    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()

    with pytest.raises(expected_exception, match=message):
        await create_document("부여 거부", "저장되면 안 되는 텍스트", **kwargs)

    assert await _document_count_by_title(mcp_database, "부여 거부") == 0


async def test_invalid_grantee_reaches_the_agent_as_a_tool_error(monkeypatch, mcp_database):
    """서비스 문구가 그대로 도구 오류로 나간다 — MCP가 다른 말로 바꾸지 않는다."""
    from mcp.server.fastmcp.exceptions import ToolError

    from openarchive.mcp_server.server import mcp

    await _seed_grantees(mcp_database)
    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()

    with pytest.raises(ToolError, match="nobody"):
        await mcp.call_tool(
            "create_document",
            {
                "title": "부여 거부",
                "content": "저장되면 안 되는 텍스트",
                "visibility": "private",
                "grant_users": ["nobody"],
            },
        )

    assert await _document_count_by_title(mcp_database, "부여 거부") == 0


async def test_create_without_grantees_makes_no_grants(monkeypatch, mcp_database):
    from openarchive.mcp_server.server import create_document

    await _seed_grantees(mcp_database)
    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()
    created = await create_document("부여 없음", "소유자만 보는 텍스트", visibility="private")

    assert await _grant_count(mcp_database, created["document_id"]) == 0
    assert created["document_id"] not in await _visible_ids(monkeypatch, "bob")


async def test_stdio_tool_property_sets_are_unchanged():
    from openarchive.mcp_server.server import mcp

    assert {tool.name: set(tool.inputSchema["properties"]) for tool in await mcp.list_tools()} == {
        "search_documents": {"query", "tags", "content_type", "k"},
        "get_document": {"document_id"},
        "list_documents": {"tag", "status", "extraction_status"},
        "create_document": {
            "title", "content", "content_type", "tags", "visibility", "grant_users", "grant_groups"
        },
    }


def _injected_tool(principal, name):
    from openarchive.mcp_server.server import build_server

    server = build_server(lambda: principal, lambda: FakeProvider())
    return server._tool_manager.get_tool(name).fn


async def test_injected_principal_controls_all_reads(monkeypatch, mcp_database):
    from openarchive.mcp_server.server import McpPrincipal
    from openarchive.services.documents import DocumentNotFound

    monkeypatch.setenv("MCP_USER_ID", "lee")
    get_settings.cache_clear()
    async with await psycopg.AsyncConnection.connect(mcp_database, autocommit=True) as conn:
        kim = await insert_test_document(
            conn, title="kim 근거", content="정합성 근거", owner_id="kim", visibility="private"
        )
        lee = await insert_test_document(
            conn, title="lee 근거", content="정합성 근거", owner_id="lee", visibility="private"
        )
        await process_all_embedding_jobs(conn, FakeProvider())
    principal = McpPrincipal("kim", "kim", True)
    for name, kwargs in [("search_documents", {"query": "정합성 근거"}), ("list_documents", {})]:
        result = await _injected_tool(principal, name)(**kwargs)
        assert {item["document_id"] for item in result["items"]} == {str(kim)}
    detail = _injected_tool(principal, "get_document")
    assert (await detail(str(kim)))["owner_id"] == "kim"
    with pytest.raises(DocumentNotFound):
        await detail(str(lee))


async def test_injected_share_reads_only_granted_documents(mcp_database):
    from openarchive.mcp_server.server import principal_from_token
    from openarchive.services.auth import PRINCIPAL_SHARE
    from openarchive.services.documents import DocumentNotFound
    from openarchive.services.shares import add_document, create_share

    async with await psycopg.AsyncConnection.connect(mcp_database, autocommit=True) as conn:
        await conn.execute("INSERT INTO users (username, password_hash) VALUES ('kim', 'unused')")
        included = await insert_test_document(conn, owner_id="kim", title="공유 근거", content="근거")
        excluded = await insert_test_document(conn, title="공개 근거", content="근거")
        share = await create_share(conn, owner="kim", name="협업")
        await add_document(conn, share["id"], included, owner="kim")
        await process_all_embedding_jobs(conn, FakeProvider())
    principal = principal_from_token({
        "kind": PRINCIPAL_SHARE, "principal": f"share:{share['id']}",
        "share_id": share["id"], "scope": "read", "username": None,
    })
    assert principal.share_id == share["id"]
    assert principal.owner is None and not principal.can_write
    for name, kwargs in [("search_documents", {"query": "근거"}), ("list_documents", {})]:
        result = await _injected_tool(principal, name)(**kwargs)
        assert {item["document_id"] for item in result["items"]} == {str(included)}
    detail = _injected_tool(principal, "get_document")
    assert (await detail(str(included)))["document_id"] == str(included)
    with pytest.raises(DocumentNotFound):
        await detail(str(excluded))


@pytest.mark.parametrize("shared", [False, True])
async def test_injected_read_scope_rejects_create_before_connection(monkeypatch, mcp_database, shared):
    from openarchive.mcp_server import server

    principal = server.principal_from_token({
        "kind": "share" if shared else "user", "username": None if shared else "kim",
        "principal": f"share:{uuid4()}" if shared else "kim",
        "share_id": uuid4(), "scope": "read",
    })
    async with await psycopg.AsyncConnection.connect(mcp_database) as conn:
        before = await (await conn.execute("SELECT count(*) FROM documents")).fetchone()
    tool = _injected_tool(principal, "create_document")
    with pytest.raises(server.WriteNotAllowed, match="^쓰기 권한이 필요합니다\\.$"):
        await tool("거부 문서", "텍스트")
    async with await psycopg.AsyncConnection.connect(mcp_database) as conn:
        after = await (await conn.execute("SELECT count(*) FROM documents")).fetchone()
    assert after == before
    await close_pool()

    def forbidden_connection():
        pytest.fail("쓰기 거부 전에 DB 연결을 빌렸습니다")

    monkeypatch.setattr(server, "connection", forbidden_connection)
    with pytest.raises(server.WriteNotAllowed, match="^쓰기 권한이 필요합니다\\.$"):
        await tool("거부 문서", "텍스트")


async def test_injected_write_owner_and_audit(monkeypatch, mcp_database):
    from test_audit import rows

    from openarchive.mcp_server.server import principal_from_token

    monkeypatch.setenv("MCP_USER_ID", "lee")
    get_settings.cache_clear()
    principal = principal_from_token({"kind": "user", "username": "kim", "scope": "read_write"})
    created = await _injected_tool(principal, "create_document")("주입 감사", "텍스트")
    assert created["owner_id"] == "kim"
    assert rows(mcp_database, created["document_id"]) == [
        ("document_created", "kim", "mcp", "주입 감사", {})
    ]
