from contextlib import asynccontextmanager
from uuid import UUID

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from openarchive.embeddings import FakeProvider
from openarchive.services.search import (
    CANDIDATE_MULTIPLIER,
    EF_SEARCH,
    MAX_K,
    SEARCH_SQL,
    search_documents,
)
from openarchive.vectors import to_pgvector_literal


class RecordingConnection:
    """실행된 문장만 받아 적고 나머지는 진짜 연결에 그대로 위임한다.

    가짜 DB가 아니다 — 모든 문장이 실제 컨테이너에서 실행된다. 검증 대상은
    "무엇을 어떤 순서로 실행했는가"뿐이라, 실행 결과는 진짜여야 한다.
    """

    def __init__(self, conn: psycopg.AsyncConnection) -> None:
        self._conn = conn
        self.statements: list[str] = []

    @asynccontextmanager
    async def transaction(self):
        async with self._conn.transaction():
            self.statements.append("BEGIN")
            yield

    async def execute(self, query, params=None):
        self.statements.append(query)
        return await self._conn.execute(query, params)


async def test_search_folder_includes_deep_descendants_and_outside_relations(worker_conn):
    provider = FakeProvider()
    folders = []
    inside = set()
    for name in ("인사", "채용", "신입"):
        cur = await worker_conn.execute(
            "INSERT INTO folders (name, parent_id, created_by, visibility) "
            "VALUES (%s, %s, 'alice', %s) RETURNING id",
            (name, folders[-1] if folders else None, None if folders else "public"),
        )
        folder = (await cur.fetchone())[0]
        folders.append(folder)
        doc = await insert_test_document(worker_conn, title=name, content="폴더 검색 [[외부]]")
        await worker_conn.execute("UPDATE documents SET folder_id = %s WHERE id = %s", (folder, doc))
        inside.add(doc)
    outside = await insert_test_document(worker_conn, title="외부", content="폴더 검색")
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(
        worker_conn, provider, query="폴더 검색", user_id="alice", folder_id=folders[0]
    )
    assert {hit.document_id for hit in hits if hit.via is None} == inside
    assert any(hit.document_id == outside and hit.via is not None for hit in hits)

    from openarchive.api.schemas import SearchRequest
    from openarchive.api.search import search

    body = SearchRequest(query="폴더 검색", folder_id=folders[0])
    assert body.folder_id == folders[0]
    response = await search(body, worker_conn, provider, "alice")
    assert {hit.document_id for hit in response.items if hit.via is None} == inside


async def test_search_hidden_folder_excludes_individually_visible_docs(worker_conn):
    provider = FakeProvider()
    cur = await worker_conn.execute(
        "INSERT INTO folders (name, created_by, visibility) "
        "VALUES ('숨김', 'bob', 'private') RETURNING id"
    )
    folder = (await cur.fetchone())[0]
    doc = await insert_test_document(worker_conn, title="개별 공개", content="폴더 검색")
    await worker_conn.execute(
        "UPDATE documents SET folder_id = %s, follows_folder = false WHERE id = %s", (folder, doc)
    )
    await process_all_embedding_jobs(worker_conn, provider)
    assert any(hit.document_id == doc for hit in await search_documents(
        worker_conn, provider, query="폴더 검색", user_id="alice"
    ))
    for selected in (folder, UUID(int=0)):
        assert await search_documents(
            worker_conn, provider, query="폴더 검색", user_id="alice", folder_id=selected
        ) == []


async def test_search_narrow_visibility_fills_k_with_iterative_scan(worker_conn):
    """1%만 열람하는 실제 HNSW 검색. 벡터는 1200개 모두 다르게 만든다."""
    provider = FakeProvider()
    query = "좁은범위"
    base = provider.embed([query])[0]
    coordinate = next(index for index, value in enumerate(base) if value == 0)
    cur = await worker_conn.execute(
        "INSERT INTO folders (name, created_by, visibility) "
        "VALUES ('검색 계획', 'writer', 'public') RETURNING id"
    )
    folder = (await cur.fetchone())[0]
    async with worker_conn.transaction():
        for index in range(1200):
            doc = await insert_test_document(
                worker_conn, title=f"후보 {index}", content=f"대목 {index}",
                owner_id="viewer" if index % 100 == 99 else "writer", visibility="private",
            )
            await worker_conn.execute(
                "UPDATE documents SET folder_id = %s, follows_folder = false WHERE id = %s",
                (folder, doc),
            )
            vector = base.copy()
            vector[coordinate] = (index + 1) / 1200
            await worker_conn.execute(
                "INSERT INTO document_chunks (document_id, version, chunk_index, content, embedding) "
                "VALUES (%s, 1, 0, %s, %s::vector)",
                (doc, f"대목 {index}", to_pgvector_literal(vector)),
            )
    cur = await worker_conn.execute("SELECT count(DISTINCT embedding::text) FROM document_chunks")
    assert (await cur.fetchone())[0] == 1200
    await worker_conn.execute("ANALYZE documents")
    await worker_conn.execute("ANALYZE document_chunks")
    recorder = RecordingConnection(worker_conn)
    hits = await search_documents(
        recorder, provider, query=query, user_id="viewer", folder_id=folder, k=10
    )
    assert len(hits) == 10
    assert all(hit.via is None for hit in hits)
    params = {
        "query": query, "identifier": None, "edition": None,
        "qvec": to_pgvector_literal(base), "tags": None, "ctype": None,
        "user": "viewer", "folder": folder, "k": 10,
    }
    async with worker_conn.transaction():
        for statement in recorder.statements[1:-1]:
            await worker_conn.execute(statement)
        cur = await worker_conn.execute("EXPLAIN " + SEARCH_SQL, params)
        plan = "\n".join(row[0] for row in await cur.fetchall())
        candidate_plan = plan.split("  CTE candidates\n", 1)[1].split("  CTE walk_ids\n", 1)[0]
        assert "Index Scan using idx_chunks_embedding" in candidate_plan, candidate_plan
        print("폴더 후보 계획 (1200 고유 벡터):", next(
            line.strip() for line in candidate_plan.splitlines()
            if "Index Scan using idx_chunks_embedding" in line
        ))


@pytest.fixture
async def worker_conn(migrated_db: str):
    """워커의 claim이 즉시 커밋되도록 autocommit 연결을 쓴다."""
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        yield conn


@pytest.fixture
async def search_conn(migrated_db: str):
    """검색 서비스가 자체 plain 트랜잭션을 열 수 있는 기본 연결이다."""
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        yield conn


async def test_matching_document_is_ranked_first(worker_conn, search_conn):
    """SQL의 거리순 정렬을 검증한다. 검색 품질은 BGE-M3의 성질이라 FakeProvider로 검증하지 않는다."""
    provider = FakeProvider()
    matching_id = await insert_test_document(
        worker_conn, title="정합성 규정", content="OpenSQL 정합성 트리거 운영 규정"
    )
    await insert_test_document(
        worker_conn, title="휴가 안내", content="연차 휴가 신청 승인 안내"
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성")

    assert hits[0].document_id == matching_id


async def test_relation_expands_search_to_a_document_outside_vector_candidates(
    worker_conn, search_conn
):
    provider = FakeProvider()
    entry_id = await insert_test_document(
        worker_conn,
        title="직접 진입점",
        content=("정합성 직접 일치 문장 " * 900),
    )
    related_id = await insert_test_document(
        worker_conn,
        title="관계로만 도달",
        content="질의 어휘가 전혀 없는 별도 문서",
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")
    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind,
             src_chunk_index, dst_chunk_index, score)
        VALUES (%s, %s, 'related', 0, 0, 0.9)
        """,
        (entry_id, related_id),
    )

    hits = await search_documents(search_conn, provider, query="정합성 직접 일치 문장", k=2)

    assert [hit.document_id for hit in hits] == [entry_id, related_id]
    assert hits[0].via is None
    assert hits[1].via is not None
    assert hits[1].via.from_document_id == entry_id
    assert hits[1].via.kind == "related"
    assert hits[1].via.depth == 1


async def test_reverse_stored_edge_expands_search(worker_conn, search_conn):
    provider = FakeProvider()
    entry_id = await insert_test_document(
        worker_conn,
        title="직접 진입점",
        content=("정합성 직접 일치 문장 " * 900),
    )
    related_id = await insert_test_document(
        worker_conn,
        title="관계로만 도달",
        content="질의 어휘가 전혀 없는 별도 문서",
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")
    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind,
             src_chunk_index, dst_chunk_index, score)
        VALUES (%s, %s, 'related', 0, 0, 0.9)
        """,
        (related_id, entry_id),
    )

    hits = await search_documents(search_conn, provider, query="정합성 직접 일치 문장", k=2)

    assert [hit.document_id for hit in hits] == [entry_id, related_id]
    assert hits[0].via is None
    assert hits[1].via is not None
    assert hits[1].via.from_document_id == entry_id
    assert hits[1].via.kind == "related"
    assert hits[1].via.depth == 1


async def test_reverse_edge_excerpt_uses_the_stored_source_chunk(
    worker_conn, search_conn
):
    provider = FakeProvider()
    entry_id = await insert_test_document(
        worker_conn,
        title="직접 진입점",
        content=("발췌 선택 직접 질의 " * 2000),
    )
    related_id = await insert_test_document(
        worker_conn,
        title="관계로만 도달",
        content="\n\n".join(
            ("질의 " * (2 * index + 1)) + ("대목 고유 어휘 " * 60) for index in range(4)
        ),
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")
    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind,
             src_chunk_index, dst_chunk_index, score)
        VALUES (%s, %s, 'related', 2, 0, 0.9)
        """,
        (related_id, entry_id),
    )

    hits = await search_documents(search_conn, provider, query="발췌 선택 직접 질의", k=4)

    assert [hit.document_id for hit in hits] == [entry_id, related_id]
    assert hits[0].via is None
    assert hits[1].via is not None
    assert hits[1].via.from_document_id == entry_id
    assert hits[1].via.kind == "related"
    assert hits[1].via.depth == 1
    assert hits[1].chunk_index == 2


async def test_an_edge_stored_in_both_directions_is_expanded_once(
    worker_conn, search_conn
):
    provider = FakeProvider()
    entry_id = await insert_test_document(
        worker_conn,
        title="직접 진입점",
        content=("정합성 직접 일치 문장 " * 900),
    )
    related_id = await insert_test_document(
        worker_conn,
        title="관계로만 도달",
        content="질의 어휘가 전혀 없는 별도 문서",
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")
    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind,
             src_chunk_index, dst_chunk_index, score)
        VALUES (%s, %s, 'related', 0, 0, 0.9),
               (%s, %s, 'related', 0, 0, 0.9)
        """,
        (entry_id, related_id, related_id, entry_id),
    )

    hits = await search_documents(search_conn, provider, query="정합성 직접 일치 문장", k=2)

    assert [hit.document_id for hit in hits] == [entry_id, related_id]
    assert hits[0].via is None
    assert hits[1].via is not None
    assert hits[1].via.from_document_id == entry_id
    assert hits[1].via.kind == "related"
    assert hits[1].via.depth == 1


async def test_trigger_built_edges_drive_search_expansion(worker_conn, search_conn):
    """트리거(014)가 만든 edge만으로 검색이 확장되는지 — step6과 step8의 결합을 본다.

    다른 그래프 테스트는 전부 `DELETE FROM document_edges` 후 손으로 INSERT한다. 그러면
    트리거가 실제로 내놓는 행의 형태(kind·청크 인덱스·방향)가 SEARCH_SQL이 소비하는
    형태와 맞는지는 어느 쪽 테스트도 보지 않는다. 여기서는 edge를 한 줄도 만들지 않는다.
    """
    provider = FakeProvider()
    entry_id = await insert_test_document(
        worker_conn,
        title="직접 진입점",
        content=("정합성 직접 일치 문장 " * 900),
    )
    neighbor_id = await insert_test_document(
        worker_conn,
        title="관계로만 도달",
        content="질의 어휘가 전혀 없는 별도 문서",
    )
    await process_all_embedding_jobs(worker_conn, provider)

    # document_edges를 손대지 않는다 — 남아 있는 행은 전부 트리거가 만든 것이다.
    # 저장은 단방향(014)이라 먼저 처리된 entry에는 (neighbor→entry) 행만 있다 — 방향을 묻지 않는다.
    edge_cur = await worker_conn.execute(
        "SELECT count(*) FROM document_edges WHERE src_document_id = %s OR dst_document_id = %s",
        (entry_id, entry_id),
    )
    assert (await edge_cur.fetchone())[0] > 0

    hits = await search_documents(search_conn, provider, query="정합성 직접 일치 문장", k=2)

    assert hits[0].document_id == entry_id
    assert hits[0].via is None
    expanded = [hit for hit in hits if hit.via is not None]
    assert [hit.document_id for hit in expanded] == [neighbor_id]
    assert expanded[0].via.from_document_id == entry_id
    assert expanded[0].via.kind in {"overlaps", "related"}
    assert expanded[0].via.depth == 1


async def test_graph_search_stops_at_depth_two_and_does_not_repeat_a_cycle(
    worker_conn, search_conn
):
    provider = FakeProvider()
    ids = [
        await insert_test_document(
            worker_conn,
            title="진입점" if index == 0 else f"관계 문서 {index}",
            content=("깊이 제한 순환 질의 " * 2000) if index == 0 else f"별도 내용 {index}",
        )
        for index in range(4)
    ]
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")
    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind,
             src_chunk_index, dst_chunk_index, score)
        VALUES (%s, %s, 'related', 0, 0, 0.9),
               (%s, %s, 'related', 0, 0, 0.8),
               (%s, %s, 'related', 0, 0, 0.7),
               (%s, %s, 'related', 0, 0, 0.6)
        """,
        # 무방향 조회에서도 0–1–2–3의 깊이를 유지하며 1→0으로 순환한다.
        (ids[0], ids[1], ids[1], ids[2], ids[2], ids[3], ids[1], ids[0]),
    )

    hits = await search_documents(search_conn, provider, query="깊이 제한 순환 질의", k=4)

    assert [hit.document_id for hit in hits[:3]] == ids[:3]
    assert ids[3] not in {hit.document_id for hit in hits}
    assert not any(
        hit.document_id == ids[0] and hit.via is not None and hit.via.kind == "related"
        for hit in hits
    )
    assert max(hit.via.depth for hit in hits if hit.via is not None) == 2


async def test_expanded_hit_excerpt_follows_the_edge_target_chunk(worker_conn, search_conn):
    """관계로 도달한 문서의 발췌 선택 규칙을 고정한다.

    `dst_chunk_index`가 NULL인 edge는 대상 문서에서 **질의에 가장 가까운 청크**를, 명시된
    edge는 **그 청크**를 발췌로 삼는다. 같은 문서에 두 edge가 닿으면 발췌가 다른 두 결과가
    된다. 청크 선택을 순회 행마다 하든 문서 축소 뒤에 하든(ADR-011 보강 6) 이 규칙은 같아야
    한다 — 다른 그래프 테스트는 전부 `dst_chunk_index = 0`이라 이 규칙을 보지 않는다.
    """
    provider = FakeProvider()
    entry_id = await insert_test_document(
        worker_conn,
        title="직접 진입점",
        content=("발췌 선택 직접 질의 " * 2000),
    )
    # 문단마다 질의 어휘 비중이 달라 청크별 거리가 서로 다르다. 그래도 진입 문서보다는
    # 멀어서 벡터 후보(LIMIT k*5)에는 들지 못하고 관계로만 도달한다.
    target_id = await insert_test_document(
        worker_conn,
        title="관계로만 도달",
        content="\n\n".join(
            ("질의 " * (2 * index + 1)) + ("대목 고유 어휘 " * 60) for index in range(4)
        ),
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")

    qvec = to_pgvector_literal(provider.embed(["발췌 선택 직접 질의"])[0])
    cur = await worker_conn.execute(
        """
        SELECT chunk_index, embedding <=> %s::vector AS dist
        FROM document_chunks WHERE document_id = %s ORDER BY dist
        """,
        (qvec, target_id),
    )
    by_distance = await cur.fetchall()
    distances = [row[1] for row in by_distance]
    assert len(by_distance) >= 2 and len(set(distances)) == len(distances), by_distance
    nearest, farthest = by_distance[0][0], by_distance[-1][0]

    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind,
             src_chunk_index, dst_chunk_index, score)
        VALUES (%s, %s, 'related', 0, NULL, 0.9),
               (%s, %s, 'related', 0, %s, 0.8)
        """,
        (entry_id, target_id, entry_id, target_id, farthest),
    )

    hits = await search_documents(search_conn, provider, query="발췌 선택 직접 질의", k=4)

    assert {hit.document_id for hit in hits if hit.via is None} == {entry_id}
    expanded = {(hit.document_id, hit.chunk_index) for hit in hits if hit.via is not None}
    assert expanded == {(target_id, nearest), (target_id, farthest)}


async def test_edges_converging_on_one_excerpt_keep_the_stronger_kind(worker_conn, search_conn):
    """같은 경유 문서의 두 관계가 같은 발췌로 수렴하면 관계 종류의 우선순위로 하나를 남긴다.

    트리거가 만드는 `overlaps`는 문서 단위라 대상 청크가 NULL이고, 위키링크 `refers`도
    NULL이다. 둘이 같은 경유 문서에서 같은 대상에 닿으면 거리·깊이·발췌가 완전히 같아
    동점이 된다. 우선순위(overlaps → related → refers → revision)가 최종 정렬에만 있고
    문서 축소에는 없으면 어느 쪽이 남는지가 물리 행 순서에 달린다 — 실 코퍼스에서 청크
    선택 순서를 바꾸자 같은 결과의 `via_kind`가 뒤집혔다 (ADR-011 보강 6).
    """
    provider = FakeProvider()
    target_id = await insert_test_document(
        worker_conn,
        title="관계로만 도달",
        content="\n\n".join(
            ("질의 " * (2 * index + 1)) + ("대목 고유 어휘 " * 60) for index in range(4)
        ),
    )
    # 위키링크가 refers 관계를, 아래 INSERT가 overlaps 관계를 같은 대상에 만든다.
    entry_id = await insert_test_document(
        worker_conn,
        title="직접 진입점",
        content=("수렴 발췌 직접 질의 " * 2000) + "\n\n[[관계로만 도달]]",
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")
    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind,
             src_chunk_index, dst_chunk_index, score)
        VALUES (%s, %s, 'overlaps', NULL, NULL, 1.0)
        """,
        (entry_id, target_id),
    )
    link_cur = await worker_conn.execute(
        "SELECT count(*) FROM document_links WHERE src_document_id = %s", (entry_id,)
    )
    assert (await link_cur.fetchone())[0] == 1

    hits = await search_documents(search_conn, provider, query="수렴 발췌 직접 질의", k=4)

    expanded = [hit for hit in hits if hit.via is not None]
    assert [hit.document_id for hit in expanded] == [target_id]
    assert expanded[0].via.kind == "overlaps"


async def test_search_adds_adjacent_context_for_an_entry_chunk(worker_conn, search_conn):
    provider = FakeProvider()
    document_id = await insert_test_document(
        worker_conn,
        title="이어짐 문서",
        content=("이어짐 맥락 복원 질의 " * 400),
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")

    hits = await search_documents(search_conn, provider, query="이어짐 맥락 복원 질의", k=2)

    assert hits[0].document_id == document_id and hits[0].via is None
    assert len(hits[0].content) > 1000


async def test_search_adds_the_previous_text_version_at_query_time(worker_conn, search_conn):
    provider = FakeProvider()
    previous_content = "개정 전 정합성 설명 " * 300
    current_content = "개정 후 정합성 설명 " * 300
    document_id = await insert_test_document(
        worker_conn,
        title="개정 문서",
        content=previous_content,
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute(
        """
        UPDATE documents
           SET version = 2, content = %s, content_hash = %s
         WHERE id = %s
        """,
        (current_content, "version-two", document_id),
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")

    hits = await search_documents(search_conn, provider, query="개정 후 정합성 설명", k=2)

    assert hits[0].via is None and hits[0].based_on_version == 2
    assert hits[1].content == previous_content
    assert hits[1].based_on_version == 1
    assert hits[1].via is not None and hits[1].via.kind == "revision"
    assert sum(hit.via is not None and hit.via.kind == "revision" for hit in hits) == 1


async def test_search_hit_contains_source_and_chunk_version(worker_conn, search_conn):
    provider = FakeProvider()
    document_id = await insert_test_document(
        worker_conn, title="근거 문서", content="OpenSQL 근거 버전"
    )
    await worker_conn.execute(
        "UPDATE documents SET filename = %s WHERE id = %s", ("evidence.md", document_id)
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hit = (await search_documents(search_conn, provider, query="OpenSQL 근거 버전"))[0]

    assert hit.filename == "evidence.md"
    assert hit.based_on_version == 1


async def test_private_document_is_hidden_from_another_user(worker_conn, search_conn):
    provider = FakeProvider()
    private_id = await insert_test_document(
        worker_conn,
        title="비공개 규정",
        content="기밀 접근통제 규정",
        owner_id="alice",
        visibility="private",
    )
    await insert_test_document(worker_conn, title="공개 안내", content="기밀 접근통제 안내")
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="기밀 접근통제", user_id="bob")

    assert private_id not in {hit.document_id for hit in hits}


async def test_owner_can_search_own_private_document(worker_conn, search_conn):
    provider = FakeProvider()
    private_id = await insert_test_document(
        worker_conn,
        title="비공개 규정",
        content="기밀 접근통제 규정",
        owner_id="alice",
        visibility="private",
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="기밀 접근통제", user_id="alice")

    assert private_id in {hit.document_id for hit in hits}


async def test_anonymous_search_returns_only_public_documents(worker_conn, search_conn):
    provider = FakeProvider()
    public_id = await insert_test_document(
        worker_conn, title="공개 규정", content="접근통제 공개 규정"
    )
    await insert_test_document(
        worker_conn,
        title="비공개 규정",
        content="접근통제 비공개 규정",
        visibility="private",
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="접근통제")

    assert [hit.document_id for hit in hits] == [public_id]


async def test_tag_filter_uses_array_overlap(worker_conn, search_conn):
    provider = FakeProvider()
    tagged_id = await insert_test_document(
        worker_conn, title="규정", content="보안 점검 규정", tags=["규정", "보안"]
    )
    await insert_test_document(
        worker_conn, title="안내", content="보안 점검 안내", tags=["안내"]
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="보안 점검", tags=["규정"])

    assert [hit.document_id for hit in hits] == [tagged_id]


async def test_content_type_filter(worker_conn, search_conn):
    provider = FakeProvider()
    pdf_id = await insert_test_document(
        worker_conn, title="PDF", content="감사 보고서", content_type="pdf"
    )
    await insert_test_document(
        worker_conn, title="Markdown", content="감사 보고서", content_type="md"
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="감사 보고서", content_type="pdf")

    assert [hit.document_id for hit in hits] == [pdf_id]


async def test_structured_filter_and_vector_ranking_apply_together(worker_conn, search_conn):
    """SQL 거리순 정렬과 태그 필터의 결합을 본다. 의미 품질은 FakeProvider의 검증 대상이 아니다."""
    provider = FakeProvider()
    both_id = await insert_test_document(
        worker_conn, title="정합성 규정", content="OpenSQL 정합성 운영", tags=["규정"]
    )
    unrelated_id = await insert_test_document(
        worker_conn, title="복지 규정", content="식대 휴가 복지", tags=["규정"]
    )
    await insert_test_document(
        worker_conn, title="태그 불일치", content="OpenSQL 정합성 운영", tags=["안내"]
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성", tags=["규정"])

    assert hits[0].document_id == both_id
    assert hits[0].score > next(hit.score for hit in hits if hit.document_id == unrelated_id)


async def test_long_document_appears_only_once(worker_conn, search_conn):
    provider = FakeProvider()
    long_id = await insert_test_document(
        worker_conn,
        title="긴 문서",
        content=("OpenSQL 정합성 " * 900),
    )
    await insert_test_document(worker_conn, title="짧은 문서", content="OpenSQL 정합성 안내")
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성", k=10)

    assert [hit.document_id for hit in hits].count(long_id) == 1


async def test_distinct_documents_are_finally_sorted_by_distance(worker_conn, search_conn):
    """SQL의 최종 거리순 정렬을 검증한다. 검색 품질은 BGE-M3의 성질이라 FakeProvider로 검증하지 않는다."""
    provider = FakeProvider()
    expected = await insert_test_document(
        worker_conn,
        document_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        title="가장 관련",
        content=("OpenSQL 정합성 " * 500),
    )
    for index in range(5):
        await insert_test_document(
            worker_conn,
            document_id=UUID(int=index + 1),
            title=f"무관 {index}",
            content=(f"휴가 복지 식대 {index} " * 200),
        )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성", k=3)

    assert hits[0].document_id == expected
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)


async def test_pending_reembedding_keeps_previous_chunks_searchable(worker_conn, search_conn):
    provider = FakeProvider()
    document_id = await insert_test_document(
        worker_conn, title="수정 문서", content="OpenSQL 정합성 이전 내용"
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute(
        """
        UPDATE documents
           SET version = version + 1, content = %s, content_hash = %s
         WHERE id = %s
        """,
        ("완전히 새로운 내용", "new-hash", document_id),
    )

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성")

    assert document_id in {hit.document_id for hit in hits}


async def test_document_without_chunks_is_not_searchable(worker_conn, search_conn):
    provider = FakeProvider()
    document_id = await insert_test_document(
        worker_conn, title="미색인", content="OpenSQL 정합성 대기"
    )

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성")

    assert document_id not in {hit.document_id for hit in hits}


@pytest.mark.parametrize("k", [0, MAX_K + 1])
async def test_k_outside_supported_range_is_rejected(search_conn, k):
    with pytest.raises(ValueError, match="k는"):
        await search_documents(search_conn, FakeProvider(), query="질의", k=k)


async def test_max_k_is_accepted(worker_conn, search_conn):
    provider = FakeProvider()
    await insert_test_document(worker_conn, title="문서", content="최대 검색 건수")
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="최대 검색 건수", k=MAX_K)

    assert len(hits) == 1


async def test_ef_search_returns_max_k_distinct_documents(worker_conn, search_conn):
    provider = FakeProvider()
    for index in range(MAX_K + 5):
        await insert_test_document(
            worker_conn, title=f"문서 {index}", content=f"공통 검색어 고유{index}"
        )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="공통 검색어", k=MAX_K)

    assert len(hits) == MAX_K


def test_candidate_limit_stays_below_ef_search():
    """ADR-011 보강 4: 과다 조회 LIMIT이 ef_search를 넘으면 에러 없이 행이 모자란다.

    등호도 안 된다 — 두 벽 사이에 여유를 두라는 것이 보강 4의 결론이다.
    """
    assert MAX_K * CANDIDATE_MULTIPLIER < EF_SEARCH


async def test_search_issues_all_tunings_inside_the_query_transaction(
    worker_conn, search_conn
):
    """search_documents가 실제로 네 SET LOCAL을 검색 쿼리와 같은 트랜잭션에 건다.

    값을 테스트 안에서 재현하면 search.py에서 지워도 통과한다. 실행된 문장을
    받아 적어, ADR-011 보강 4·5와 JIT 끄기(ADR-044) 준수를 구현 쪽에서 검증한다.
    """
    provider = FakeProvider()
    await insert_test_document(worker_conn, title="튜닝", content="검색 튜닝 확인")
    await process_all_embedding_jobs(worker_conn, provider)
    recorder = RecordingConnection(search_conn)

    await search_documents(recorder, provider, query="검색 튜닝 확인")

    assert recorder.statements[0] == "BEGIN"
    assert recorder.statements[1] == f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"
    assert recorder.statements[2] == "SET LOCAL random_page_cost = 1.1"
    assert recorder.statements[3] == "SET LOCAL jit = off"
    assert recorder.statements[4] == "SET LOCAL hnsw.iterative_scan = strict_order"
    assert recorder.statements[5] == SEARCH_SQL


async def test_search_tuning_does_not_leak_past_the_transaction(worker_conn):
    """SET LOCAL이므로 트랜잭션이 끝나면 세션 값이 되돌아온다.

    OpenProxy는 백엔드를 넘길 때 RESET ALL만 하므로(ADR-022), 세션에 남는 값이
    다음 클라이언트로 새는지가 실제 위험이다. autocommit 연결을 쓰는 이유는
    앞선 문장이 트랜잭션을 열어두면 conn.transaction()이 SAVEPOINT가 되어
    SET LOCAL의 범위가 바깥 트랜잭션으로 넓어지기 때문이다.
    """
    provider = FakeProvider()
    await insert_test_document(worker_conn, title="튜닝", content="검색 튜닝 확인")
    await process_all_embedding_jobs(worker_conn, provider)
    before_iterative = (await (await worker_conn.execute("SHOW hnsw.iterative_scan")).fetchone())[0]
    before_ef = (await (await worker_conn.execute("SHOW hnsw.ef_search")).fetchone())[0]
    before_rpc = (await (await worker_conn.execute("SHOW random_page_cost")).fetchone())[0]
    before_jit = (await (await worker_conn.execute("SHOW jit")).fetchone())[0]

    await search_documents(worker_conn, provider, query="검색 튜닝 확인")

    after_ef = (await (await worker_conn.execute("SHOW hnsw.ef_search")).fetchone())[0]
    after_rpc = (await (await worker_conn.execute("SHOW random_page_cost")).fetchone())[0]
    after_jit = (await (await worker_conn.execute("SHOW jit")).fetchone())[0]
    after_iterative = (await (await worker_conn.execute("SHOW hnsw.iterative_scan")).fetchone())[0]
    assert after_iterative == before_iterative != "strict_order"
    assert after_ef == before_ef != str(EF_SEARCH)
    assert after_rpc == before_rpc != "1.1"
    assert after_jit == before_jit != "off"


async def test_explain_contains_structured_filters_and_vector_ordering(worker_conn, migrated_db):
    provider = FakeProvider()
    for index in range(10):
        await insert_test_document(
            worker_conn,
            title=f"계획 문서 {index}",
            content=f"OpenSQL 정합성 계획 {index}",
            tags=["규정"],
        )
    await process_all_embedding_jobs(worker_conn, provider)
    params = {
        "folder": None,
        "query": "OpenSQL 정합성",
        "edition": None,
        "identifier": None,
        "qvec": to_pgvector_literal(provider.embed(["OpenSQL 정합성"])[0]),
        "tags": ["규정"],
        "ctype": None,
        "user": "alice",
        "k": 5,
    }

    async with (
        await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn,
        conn.transaction(),
    ):
        await conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
        await conn.execute("SET LOCAL random_page_cost = 1.1")
        cur = await conn.execute("EXPLAIN " + SEARCH_SQL, params)
        plan = "\n".join(row[0] for row in await cur.fetchall())

    # 프로덕션과 같은 조건으로 계획을 본다. 인덱스 선택은 검증 대상이 아니다 —
    # 로컬의 열 몇 건짜리 데이터에서는 Seq Scan이 실제로 더 싸고, HNSW 선택 여부는
    # 실 VM 6000행 실측이 판정했다 (ADR-011 보강 5). 여기서 확인하는 것은
    # 정형 필터와 벡터 정렬이 **하나의 계획**에 결합된다는 사실뿐이다.
    assert "visibility" in plan and "owner_id" in plan, plan
    assert "document_grants" in plan and "group_members" in plan, plan
    assert "tags" in plan and "<=>" in plan, plan


@pytest.mark.parametrize(
    ('query', 'title', 'promoted'),
    [
        ('여비지급규칙', '여비지급규칙', True),
        ('채용관리지침 제10조 내용을 찾아줘', '채용관리지침', True),
        ('2022판 여비지급규칙 문서를 찾아줘', '여비지급규칙 (2022판)', True),
        ('2022년판 여비지급규칙 문서를 찾아줘', '여비지급규칙 (2022판)', True),
        ('여비지급규칙 내용을 찾아줘', '여비지급규칙', False),
        ('2025판 여비지급규칙 문서를 찾아줘', '여비지급규칙', False),
        ('2022판 2025판 여비지급규칙 비교', '여비지급규칙 (2022판)', False),
    ],
)
async def test_title_priority_preserves_cosine_score(worker_conn, search_conn, query, title, promoted):
    provider = FakeProvider()
    target = await insert_test_document(worker_conn, title=title, content='다른 내용')
    closest = await insert_test_document(worker_conn, title='본문 일치', content=query)
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query=query, k=1)
    direct = [hit for hit in hits if hit.via is None]
    assert direct[0].document_id == (target if promoted else closest)
    assert direct[0].score == pytest.approx(0 if promoted else 1, abs=1e-6)
    assert all(hit.via is None for hit in hits[:len(direct)])


@pytest.mark.parametrize('excluded_by', ['visibility', 'tags', 'content_type'])
async def test_title_priority_cannot_bypass_filters(worker_conn, search_conn, excluded_by):
    provider = FakeProvider()
    query = '여비지급규칙'
    hidden = await insert_test_document(
        worker_conn, title=query, content='다른 내용',
        visibility='private' if excluded_by == 'visibility' else 'public',
        tags=['다른 태그'], content_type='txt',
    )
    allowed = await insert_test_document(
        worker_conn, title='본문 일치', content=query, tags=['규정'], content_type='md',
    )
    await process_all_embedding_jobs(worker_conn, provider)
    hits = await search_documents(
        search_conn, provider, query=query, user_id='bob',
        tags=['규정'] if excluded_by == 'tags' else None,
        content_type='md' if excluded_by == 'content_type' else None,
    )
    assert hits[0].document_id == allowed
    assert hidden not in [hit.document_id for hit in hits]


@pytest.mark.parametrize(
    ('query', 'marker', 'expected'),
    [
        ('ADR-006 결정 내용', 'ADR-006:', True),
        ('제9조 채용 내용', '제9조(채용공고)', True),
        ('제 9 조 채용 내용', '제9조(채용공고)', True),
        ('ADR-006 결정 내용', 'ADR-0061:', False),
        ('ADR-006 ADR-010 비교', 'ADR-006:', False),
        ('제9조 제10조 비교', '제9조(채용공고)', False),
    ],
)
async def test_identifier_selects_excerpt_without_changing_rank_or_score(
    worker_conn, search_conn, query, marker, expected
):
    provider = FakeProvider()
    did = await insert_test_document(
        worker_conn, title='번호 문서',
        content='일반 설명 문장 ' * 1500 + '\n\n' + marker + ' 별도 근거 내용',
    )
    await process_all_embedding_jobs(worker_conn, provider)
    params = {'qvec': to_pgvector_literal(provider.embed([query])[0]), 'id': did}
    # SQL 발췌 선택을 검증하므로 첫 청크가 확실히 가장 가까운 합성 벡터를 둔다.
    await worker_conn.execute(
        "UPDATE document_chunks SET embedding = CASE WHEN chunk_index = 0 "
        "THEN %(qvec)s::vector ELSE %(other)s::vector END WHERE document_id = %(id)s",
        {**params, 'other': to_pgvector_literal(provider.embed(['무관한 벡터'])[0])},
    )
    nearest = await (await worker_conn.execute(
        'SELECT chunk_index, 1 - (embedding <=> %(qvec)s::vector) '
        'FROM document_chunks WHERE document_id = %(id)s ORDER BY embedding <=> %(qvec)s::vector, chunk_index LIMIT 1',
        params,
    )).fetchone()
    assert nearest[0] == 0
    hits = await search_documents(search_conn, provider, query=query)
    hit = next(h for h in hits if h.document_id == did and h.via is None)
    assert hit.score == pytest.approx(nearest[1], abs=1e-6)
    if expected:
        assert marker in hit.content
        assert hit.preview is not None and marker in hit.preview
        assert hit.chunk_index > 1
        chunk = await (await worker_conn.execute(
            'SELECT content, version FROM document_chunks WHERE document_id = %s AND chunk_index = %s',
            (did, hit.chunk_index),
        )).fetchone()
        assert marker in chunk[0]
        assert hit.based_on_version == chunk[1]
    else:
        assert hit.chunk_index == 0
        assert marker not in hit.content

async def test_natural_question_selects_excerpt_from_same_version_without_reordering(
    worker_conn, search_conn,
):
    provider = FakeProvider()
    query = '수정 문서 검색'
    did = await insert_test_document(
        worker_conn, title='버전 관리',
        content=('설정 파일 연결 안내.\n\n' * 100) +
                ('수정 문서 검색은 이전 버전을 유지합니다.\n\n' * 15),
    )
    await process_all_embedding_jobs(worker_conn, provider)
    qvec = to_pgvector_literal(provider.embed([query])[0])
    # Document rank comes from the first chunk, but the answer is a different passage.
    await worker_conn.execute(
        'UPDATE document_chunks SET embedding=%s::vector WHERE document_id=%s',
        (qvec, did),
    )
    hits = await search_documents(search_conn, provider, query=query)
    hit = next(h for h in hits if h.document_id == did and h.via is None)
    assert '이전 버전을 유지합니다' in hit.content
    assert hit.preview is not None
    assert not hit.preview.startswith('설정 파일')
    assert '이전 버전을 유지합니다' in hit.preview
    assert hit.score == pytest.approx(1.0, abs=1e-6)
    stored = await (await worker_conn.execute(
        'SELECT content,version FROM document_chunks WHERE document_id=%s AND chunk_index=%s',
        (did, hit.chunk_index),
    )).fetchone()
    assert hit.based_on_version == stored[1] == 1
    assert '이전 버전을 유지합니다' in stored[0]

async def test_excerpt_does_not_use_current_text_during_reembedding(worker_conn, search_conn):
    provider = FakeProvider()
    did = await insert_test_document(worker_conn, title='이전 판', content='수정 문서 검색은 이전 판의 근거를 사용합니다.')
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("UPDATE documents SET content='수정 문서 검색 새판 비밀 표식',content_hash='new-excerpt-hash' WHERE id=%s", (did,))
    hits = await search_documents(search_conn, provider, query='수정 문서 검색')
    hit = next(h for h in hits if h.document_id == did and h.via is None)
    assert hit.based_on_version == 1
    assert '새판 비밀 표식' not in hit.content
    assert '새판 비밀 표식' not in (hit.preview or '')
    assert '이전 판의 근거' in hit.content

async def test_preview_selection_only_embeds_the_query(worker_conn, search_conn):
    class CountingProvider(FakeProvider):
        def __init__(self):
            self.calls = []

        def embed(self, texts):
            self.calls.append(texts)
            return super().embed(texts)

    provider = CountingProvider()
    await insert_test_document(worker_conn, title='질의 처리', content='문서 수정 처리 근거.\n\n' * 120)
    await process_all_embedding_jobs(worker_conn, provider)
    provider.calls.clear()
    hits = await search_documents(search_conn, provider, query='문서 수정 처리')
    assert hits and hits[0].preview
    assert provider.calls == [['문서 수정 처리']]
    assert len(hits[0].preview) <= 300
    assert hits[0].preview in hits[0].content


async def test_search_keeps_alternative_passages_from_filtered_vector_candidates(
    worker_conn, search_conn
):
    """문서당 최고 청크 밖의 답을 보존하되 새 본문·권한 밖 후보를 섞지 않는다."""
    import math

    from openarchive.services.search import MAX_PASSAGES

    class AxisProvider:
        def __init__(self):
            self.calls = []

        def embed(self, texts):
            self.calls.append(texts)
            return [[1.0] + [0.0] * 1023 for _ in texts]

    provider = AxisProvider()
    parts = [f"운영 설정 {i}." for i in range(12)]
    parts[7] = "답변 표식: 새 임베딩 완료 전에는 이전 청크로 검색한다."
    did = await insert_test_document(
        worker_conn, title="운영", content="\n\n".join(parts),
        owner_id="alice", visibility="private", tags=["candidate-test"],
    )
    for i, part in enumerate(parts):
        similarity = 0.99 - i * 0.01
        vector = [similarity, math.sqrt(1 - similarity ** 2)] + [0.0] * 1022
        await worker_conn.execute(
            "INSERT INTO document_chunks(document_id,version,chunk_index,content,embedding) "
            "VALUES(%s,1,%s,%s,%s::vector)",
            (did, i * 5, part, to_pgvector_literal(vector)),
        )
    hidden = await insert_test_document(
        worker_conn, title="숨김", content="비공개 표식", owner_id="bob",
        visibility="private", tags=["candidate-test"],
    )
    filtered = await insert_test_document(
        worker_conn, title="다른 태그", content="다른 태그 표식", tags=["other"],
    )
    wrong_type = await insert_test_document(
        worker_conn, title="다른 유형", content="다른 유형 표식", content_type="pdf",
        tags=["candidate-test"],
    )
    for other in [hidden, filtered, wrong_type]:
        await worker_conn.execute(
            "INSERT INTO document_chunks(document_id,version,chunk_index,content,embedding) "
            "VALUES(%s,1,0,'노출 금지 표식',%s::vector)",
            (other, to_pgvector_literal([1.0] + [0.0] * 1023)),
        )
    await worker_conn.execute(
        "UPDATE documents SET content='새판 표식', content_hash='new-candidate-version' WHERE id=%s",
        (did,),
    )

    hits = await search_documents(
        search_conn, provider, query="본문 동작 질문", user_id="alice",
        tags=["candidate-test"], content_type="md", k=3,
    )
    direct = [hit for hit in hits if hit.via is None]
    assert [hit.document_id for hit in direct] == [did]
    hit = direct[0]
    assert "답변 표식" not in hit.content
    assert len(hit.passages) == MAX_PASSAGES == 8
    assert [p.chunk_index for p in hit.passages] == [i * 5 for i in range(8)]
    assert any("답변 표식" in p.content for p in hit.passages)
    assert all(p.based_on_version == hit.based_on_version == 1 for p in hit.passages)
    assert all("새판 표식" not in p.content and "노출 금지" not in p.content for p in hit.passages)
    assert all(a.score > b.score for a, b in zip(hit.passages, hit.passages[1:]))
    assert all(not h.passages for h in hits if h.via is not None)
    narrow = await search_documents(
        search_conn, provider, query="본문 동작 질문", user_id="alice",
        tags=["candidate-test"], content_type="md", k=1,
    )
    assert [p.chunk_index for p in narrow[0].passages] == [i * 5 for i in range(CANDIDATE_MULTIPLIER)]
    assert all("답변 표식" not in p.content for p in narrow[0].passages)
    assert provider.calls == [["본문 동작 질문"], ["본문 동작 질문"]]


async def test_alternative_context_recovers_a_gap_outside_vector_candidates(worker_conn, search_conn):
    """순위·기본 발췌는 유지하면서 가까운 청크 사이의 근거도 반환한다."""
    class AxisProvider:
        def embed(self, texts):
            return [[1.0] + [0.0] * 1023 for _ in texts]

    provider = AxisProvider()
    did = await insert_test_document(worker_conn, title="전달 보장", content="이전 판", tags=["gap"])
    for index, text in enumerate(["작업 소개", "처리 절차", "전달이 유실돼도 다음 폴링이 처리한다"]):
        vector = [1.0, 0.0] if index == 0 else [0.0, 1.0]
        await worker_conn.execute(
            "INSERT INTO document_chunks(document_id,version,chunk_index,content,embedding) "
            "VALUES(%s,1,%s,%s,%s::vector)",
            (did, index, text, to_pgvector_literal(vector + [0.0] * 1022)),
        )
    for index in range(CANDIDATE_MULTIPLIER):
        other = await insert_test_document(worker_conn, title=f"다른 문서 {index}", content="소개", tags=["gap"])
        await worker_conn.execute(
            "INSERT INTO document_chunks(document_id,version,chunk_index,content,embedding) "
            "VALUES(%s,1,0,'다른 설명',%s::vector)",
            (other, to_pgvector_literal([0.9, 0.1] + [0.0] * 1022)),
        )
    await worker_conn.execute("UPDATE documents SET content='새판 비밀',content_hash='new-gap' WHERE id=%s", (did,))
    hit = (await search_documents(search_conn, provider, query="전달 보장", tags=["gap"], k=1))[0]
    assert hit.document_id == did and hit.score == pytest.approx(1.0)
    assert "폴링" not in hit.content
    assert len(hit.passages) == 1 and hit.passages[0].chunk_index == 0
    assert "다음 폴링이 처리한다" in hit.passages[0].content
    assert "새판 비밀" not in hit.passages[0].content
    assert hit.passages[0].based_on_version == 1


def test_preview_can_start_at_answer_paragraph_without_losing_word_suffixes():
    from openarchive.services.search import _preview_index, _preview_options

    body = "파일 설정 안내. " * 18 + "\n\n원본 파일은 교체할 때마다 보관합니다. 이전 판 삭제 정책이 없어 교체할수록 용량이 누적됩니다."
    options = _preview_options(body)
    winner = _preview_index("파일을 교체하면 이전 파일과 저장 용량은 어떻게 돼?", options)
    assert winner is not None
    assert "이전 판 삭제 정책이 없어" in options[winner]
    assert "용량이 누적됩니다" in options[winner]
    assert all(len(option) <= 300 and option in body for option in options)


async def test_preview_preserves_an_answer_crossing_neighbor_chunk_boundary(worker_conn, search_conn):
    provider = FakeProvider()
    did = await insert_test_document(worker_conn, title="원본 보관", content="원본 보관")
    texts = ["원본 파일 교체마다 이전 판을 보관합니다.", "삭제 정책이 없어 저장 용량은 누적됩니다."]
    for index, text in enumerate(texts):
        await worker_conn.execute(
            "INSERT INTO document_chunks(document_id,version,chunk_index,content,embedding) "
            "VALUES(%s,1,%s,%s,%s::vector)",
            (did, index, text, to_pgvector_literal(provider.embed(["원본 파일 교체 저장 용량"])[0])),
        )
    hit = (await search_documents(search_conn, provider, query="원본 파일 교체 저장 용량", k=1))[0]
    assert "이전 판을 보관합니다" in hit.preview
    assert "저장 용량은 누적됩니다" in hit.preview
    assert hit.preview in hit.content
    assert hit.chunk_index == 0


def test_merge_candidate_contexts_preserves_each_source_chunk_once():
    from openarchive.services.search import _merge_passages

    items = [
        [3, [[1, "소개"], [2, "첫 근거"], [3, "중심"], [4, "두 번째 근거"]], 7, 0.9],
        [5, [[3, "중심"], [4, "두 번째 근거"], [5, "결론"]], 7, 0.8],
        [12, [[11, "별도 소개"], [12, "별도 근거"]], 7, 0.7],
    ]
    passages = _merge_passages(items)
    assert len(passages) == 2
    assert passages[0].chunk_index == 3 and passages[0].score == 0.9
    assert passages[0].content == "소개\n\n첫 근거\n\n중심\n\n두 번째 근거\n\n결론"
    assert passages[1].content == "별도 소개\n\n별도 근거"
    assert all(p.based_on_version == 7 for p in passages)
    assert _merge_passages([]) == ()


def test_merge_context_uses_candidate_anchor_when_source_indexes_have_a_gap():
    from openarchive.services.search import _merge_passages

    passages = _merge_passages([[0, [[0, "앞 근거"], [2, "뒤 근거"]], 1, 0.9]])
    assert len(passages) == 1
    assert passages[0].chunk_index == 0
    assert passages[0].content == "앞 근거\n\n뒤 근거"


def test_table_preview_keeps_previous_comparison_row_within_the_limit():
    from openarchive.services.search import _table_preview_context

    same = "| 같은 키 + 같은 요청 | 기존 문서를 돌려준다 |"
    different = "| 같은 키 + 다른 요청 | 422로 거부한다 |"
    following = "| 키 없음 | 새 문서를 만든다 |"
    body = f"| 상황 | 응답 |\n|---|---|\n{same}\n{different}\n{following}"
    preview = body[body.index(different):]
    enriched = _table_preview_context(body, preview)
    assert enriched == same + "\n" + different
    assert enriched in body and len(enriched) <= 300
    assert _table_preview_context(body, body[body.index(same):]) == body[body.index(same):]


def test_table_preview_does_not_take_a_partial_row_or_unrelated_prose():
    from openarchive.services.search import _table_preview_context

    row = "| 조건 | 결과 |"
    long_row = "| 다른 조건 | " + "긴 설명" * 100 + " |"
    for prefix in ["일반 설명", "|---|---|", long_row]:
        body = prefix + "\n" + row
        assert _table_preview_context(body, row) == row
    body = "| 앞 조건 | 앞 결과 |\n" + row
    assert _table_preview_context(body, "조건 | 결과 |") == "조건 | 결과 |"
    assert _table_preview_context(body, "없는 대목") == "없는 대목"
    assert _table_preview_context(row, row) == row


async def test_table_preview_keeps_source_chunk_version_and_vector_score(worker_conn, search_conn):
    provider = FakeProvider()
    same = "| 같은 요청 | 기존 문서를 201로 반환 |"
    different = "| 파일 내용 변경 다른 요청 | 422로 거부 |"
    body = "| 조건 | 결과 |\n|---|---|\n" + same + "\n" + different
    query = "파일 내용 변경 다른 요청"
    did = await insert_test_document(worker_conn, title="재시도 조건", content=body)
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute(
        'UPDATE document_chunks SET embedding=%s::vector WHERE document_id=%s',
        (to_pgvector_literal(provider.embed([query])[0]), did),
    )
    hit = (await search_documents(search_conn, provider, query=query, k=1))[0]
    assert hit.document_id == did and hit.via is None
    assert hit.preview == same + "\n" + different
    assert hit.preview in hit.content
    assert hit.chunk_index == 0 and hit.based_on_version == 1
    assert hit.score == pytest.approx(1.0, abs=1e-6)


def test_table_preview_does_not_wrap_to_the_end_for_an_indented_first_row():
    from openarchive.services.search import _table_preview_context

    body = "  | 첫 조건 | 첫 결과 |\n| 마지막 조건 | 마지막 결과 | "
    preview = body.strip()
    assert _table_preview_context(body, preview) == preview
