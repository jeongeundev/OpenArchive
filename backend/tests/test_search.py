from contextlib import asynccontextmanager
from uuid import UUID

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from app.embeddings import FakeProvider
from app.services.search import (
    CANDIDATE_MULTIPLIER,
    EF_SEARCH,
    MAX_K,
    SEARCH_SQL,
    search_documents,
)
from app.vectors import to_pgvector_literal


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


async def test_trigger_built_edges_drive_search_expansion(worker_conn, search_conn):
    """008 트리거가 만든 edge만으로 검색이 확장되는지 — step6과 step8의 결합을 본다.

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
    edge_cur = await worker_conn.execute(
        "SELECT count(*) FROM document_edges WHERE src_document_id = %s", (entry_id,)
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
        (ids[0], ids[1], ids[1], ids[2], ids[2], ids[3], ids[2], ids[0]),
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


async def test_search_issues_both_tunings_inside_the_query_transaction(
    worker_conn, search_conn
):
    """search_documents가 실제로 두 SET LOCAL을 검색 쿼리와 같은 트랜잭션에 건다.

    두 값을 테스트 안에서 재현하면 search.py에서 지워도 통과한다. 실행된 문장을
    받아 적어, ADR-011 보강 4·5 준수를 구현 쪽에서 검증한다.
    """
    provider = FakeProvider()
    await insert_test_document(worker_conn, title="튜닝", content="검색 튜닝 확인")
    await process_all_embedding_jobs(worker_conn, provider)
    recorder = RecordingConnection(search_conn)

    await search_documents(recorder, provider, query="검색 튜닝 확인")

    assert recorder.statements[0] == "BEGIN"
    assert recorder.statements[1] == f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"
    assert recorder.statements[2] == "SET LOCAL random_page_cost = 1.1"
    assert recorder.statements[3] == SEARCH_SQL


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
    before_ef = (await (await worker_conn.execute("SHOW hnsw.ef_search")).fetchone())[0]
    before_rpc = (await (await worker_conn.execute("SHOW random_page_cost")).fetchone())[0]

    await search_documents(worker_conn, provider, query="검색 튜닝 확인")

    after_ef = (await (await worker_conn.execute("SHOW hnsw.ef_search")).fetchone())[0]
    after_rpc = (await (await worker_conn.execute("SHOW random_page_cost")).fetchone())[0]
    assert after_ef == before_ef != str(EF_SEARCH)
    assert after_rpc == before_rpc != "1.1"


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
    assert "tags" in plan and "<=>" in plan, plan
