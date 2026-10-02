"""공유 주체(share:<uuid>)의 열람 범위 (ADR-044 「공유」 결정 2·3, #97 c).

공유 주체에게는 그 공유에 부여된 문서만 있다 — 조직 공개 문서라도 부여가 없으면 없다.
술어 하나(`VISIBLE_TO_USER`)가 전 경로에 걸리므로 검색·목록·관계·집계·링크를 한 데이터로 함께 본다.
"""

from uuid import uuid4

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from openarchive.embeddings import FakeProvider
from openarchive.services.clusters import get_clusters
from openarchive.services.diagnostics import get_diagnostics
from openarchive.services.documents import (
    DocumentNotFound,
    document_progress,
    get_document,
    get_document_version,
    list_documents,
)
from openarchive.services.links import find_backlinks, resolve_links
from openarchive.services.related import find_related, suggest_tags
from openarchive.services.search import SEARCH_SQL, search_documents
from openarchive.services.visibility import SHARE_PRINCIPAL_PREFIX, share_principal
from openarchive.vectors import to_pgvector_literal

QUERY = "OpenSQL 공유 경계 문서"


@pytest.fixture
async def worker_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        yield conn


@pytest.fixture
async def visibility_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        yield conn


async def add_user(conn, username: str):
    cur = await conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, 'scrypt-hash') RETURNING id",
        (username,),
    )
    return (await cur.fetchone())[0]


async def add_share(conn, owner_user_id, name: str, document_ids):
    cur = await conn.execute(
        "INSERT INTO shares (owner_user_id, name) VALUES (%s, %s) RETURNING id",
        (owner_user_id, name),
    )
    share_id = (await cur.fetchone())[0]
    for document_id in document_ids:
        await conn.execute(
            "INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)",
            (document_id, share_id),
        )
    return share_id


async def add_edge(conn, src, dst, kind="related", score=0.8):
    await conn.execute(
        """
        INSERT INTO document_edges (src_document_id, dst_document_id, kind, score)
        VALUES (%s, %s, %s, %s)
        """,
        (src, dst, kind, score),
    )


@pytest.fixture
async def shared(worker_conn):
    """문서 100건 중 5건(조직 공개 3 · 제한 2)을 공유 S에 부여한다.

    관계는 워커가 만든 것을 지우고 손으로 둔다 — 기대값을 계산할 수 있어야 "밖이 안 보인다"가
    공허하지 않다. 공유 안팎을 잇는 관계·위키링크와, 밖의 문서와 같은 텍스트를 일부러 둔다.
    """
    provider = FakeProvider()
    alice = await add_user(worker_conn, "alice")
    await add_user(worker_conn, "bob")
    await add_user(worker_conn, "carol")

    outside = {}
    for i in range(5, 100):
        outside[i] = await insert_test_document(
            worker_conn,
            title=f"외부 {i}",
            content=f"바깥 기록 {i} 별개 내용",
            owner_id="alice" if i % 2 else "bob",
            visibility="public" if i % 3 else "private",
            tags=["외부전용"],
        )
    # 공유 문서 S2와 같은 텍스트 — 공유 주체에게 동일 텍스트 후보로 잡히면 밖이 샌 것이다.
    same_text_as_s2 = f"{QUERY} 공유 둘"
    outside_twin = await insert_test_document(
        worker_conn, title="외부 쌍둥이", content=same_text_as_s2, tags=["외부전용"]
    )
    specs = [
        ("공유 0", f"{QUERY} 공유 영 [[외부 50]] [[공유 1]]", "alice", "public", ["공유"]),
        ("공유 1", f"{QUERY} 공유 하나 [[없는 문서]]", "bob", "public", []),
        ("공유 2", same_text_as_s2, "alice", "public", ["공유"]),
        ("공유 3", f"{QUERY} 공유 셋", "alice", "private", ["공유"]),
        ("공유 4", f"{QUERY} 공유 넷", "bob", "private", []),
    ]
    inside = [
        await insert_test_document(
            worker_conn, title=title, content=content, owner_id=owner,
            visibility=visibility, tags=tags,
        )
        for title, content, owner, visibility, tags in specs
    ]
    await process_all_embedding_jobs(worker_conn, provider)

    await worker_conn.execute("DELETE FROM document_edges")
    await add_edge(worker_conn, inside[0], inside[1])
    await add_edge(worker_conn, inside[0], outside[10])
    await add_edge(worker_conn, inside[3], outside[20], kind="overlaps", score=1.0)
    await add_edge(worker_conn, outside[30], inside[2])
    await add_edge(worker_conn, outside[40], outside[41])

    share_id = await add_share(worker_conn, alice, "협력사", inside)
    return provider, share_id, inside, outside, outside_twin


async def test_fixture_actually_links_inside_and_outside(worker_conn, shared):
    """관계·위키링크가 실제로 공유 밖을 가리켜야 아래 단언들이 공허하지 않다."""
    _, _, inside, _, _ = shared
    cur = await worker_conn.execute(
        """
        SELECT count(*) FILTER (WHERE NOT (src_document_id = ANY(%(ids)s)
                                           AND dst_document_id = ANY(%(ids)s)))
        FROM document_edges
        WHERE src_document_id = ANY(%(ids)s) OR dst_document_id = ANY(%(ids)s)
        """,
        {"ids": inside},
    )
    assert (await cur.fetchone())[0] == 3
    cur = await worker_conn.execute(
        "SELECT count(*) FROM document_links WHERE src_document_id = ANY(%s)", (inside,)
    )
    assert (await cur.fetchone())[0] == 3
    cur = await worker_conn.execute("SELECT count(*) FROM documents")
    assert (await cur.fetchone())[0] == 101


async def test_share_principal_sees_only_its_granted_documents(visibility_conn, shared):
    provider, share_id, inside, outside, _ = shared
    principal = share_principal(share_id)

    hits = await search_documents(visibility_conn, provider, query=QUERY, user_id=principal)
    listed = await list_documents(visibility_conn, user_id=principal)
    progress = await document_progress(visibility_conn, user_id=principal)

    # 그래프 순회가 공유 0 → 외부 10, 공유 2 ← 외부 30으로 나가지 않는다.
    assert {hit.document_id for hit in hits} == set(inside)
    # 같은 질의로 alice에게는 공유 밖 문서가 나온다 — 막을 대상이 실제로 있다.
    alice_hits = await search_documents(visibility_conn, provider, query=QUERY, user_id="alice")
    assert {hit.document_id for hit in alice_hits} - set(inside)
    assert {row["id"] for row in listed} == set(inside)
    assert sum(progress.values()) == 5

    # 같은 데이터에서 사용자 alice에게는 밖으로 나가는 관계가 보인다 — 단언이 공허하지 않다.
    alice_related = await find_related(visibility_conn, document_id=inside[0], user_id="alice")
    assert outside[10] in {item.document_id for item in alice_related.items}


async def test_relations_and_tags_stay_inside_the_share(visibility_conn, shared):
    _, share_id, inside, _, _ = shared
    principal = share_principal(share_id)

    related = await find_related(visibility_conn, document_id=inside[0], user_id=principal)
    tags = await suggest_tags(visibility_conn, document_id=inside[0], user_id=principal)

    assert {item.document_id for item in related.items} == {inside[1]}
    assert related.identical == []
    assert "외부전용" not in {item.tag for item in tags.items}

    twin = await find_related(visibility_conn, document_id=inside[2], user_id=principal)
    assert twin.items == []
    assert twin.identical == []


async def test_aggregates_count_only_the_share(visibility_conn, shared):
    _, share_id, inside, _, _ = shared
    principal = share_principal(share_id)

    diagnostics = await get_diagnostics(visibility_conn, user_id=principal)
    clusters = await get_clusters(visibility_conn, user_id=principal)

    # 보이는 관계는 공유 0–공유 1 하나뿐이다. 나머지는 밖과만 이어져 있어 고아로 보인다.
    assert diagnostics.orphans.count == 3
    assert {item.document_id for item in diagnostics.orphans.items} == {
        inside[2], inside[3], inside[4]
    }
    assert diagnostics.uncategorized.count == 2
    # 외부 50(실재하지만 밖)과 없는 문서가 똑같이 깨진 링크다.
    assert diagnostics.broken_links.count == 2
    assert {link.target_title for link in diagnostics.broken_links.items} == {
        "외부 50", "없는 문서"
    }
    assert diagnostics.duplicates.identical.count == 0
    assert diagnostics.duplicates.overlaps.count == 0

    assert sum(cluster.size for cluster in clusters.clusters) == 5
    assert {
        document.document_id for cluster in clusters.clusters for document in cluster.documents
    } == set(inside)


async def test_a_link_outside_the_share_looks_like_a_missing_document(visibility_conn, shared):
    _, share_id, inside, _, _ = shared
    principal = share_principal(share_id)

    from_s0 = await resolve_links(visibility_conn, document_id=inside[0], user_id=principal)
    from_s1 = await resolve_links(visibility_conn, document_id=inside[1], user_id=principal)
    backlinks = await find_backlinks(visibility_conn, document_id=inside[1], user_id=principal)

    assert {(link.title, link.document_id) for link in from_s0} == {
        ("외부 50", None), ("공유 1", inside[1])
    }
    assert [(link.title, link.document_id) for link in from_s1] == [("없는 문서", None)]
    assert [link.document_id for link in backlinks] == [inside[0]]


@pytest.mark.parametrize("which", [10, 50, 61])
async def test_outside_documents_do_not_exist_for_the_share(visibility_conn, shared, which):
    """외부 10은 관계로, 외부 50은 링크로 이어진 조직 공개 문서다. 외부 61은 제한 문서다."""
    _, share_id, _, outside, _ = shared
    principal = share_principal(share_id)
    target = outside[which]

    with pytest.raises(DocumentNotFound):
        await get_document(visibility_conn, target, user_id=principal)
    with pytest.raises(DocumentNotFound):
        await get_document_version(visibility_conn, target, version=1, user_id=principal)
    with pytest.raises(DocumentNotFound):
        await resolve_links(visibility_conn, document_id=target, user_id=principal)
    with pytest.raises(DocumentNotFound):
        await find_backlinks(visibility_conn, document_id=target, user_id=principal)
    with pytest.raises(DocumentNotFound):
        await find_related(visibility_conn, document_id=target, user_id=principal)


async def test_shares_do_not_see_each_other(worker_conn, visibility_conn, shared):
    _, share_id, inside, outside, _ = shared
    cur = await worker_conn.execute("SELECT id FROM users WHERE username = 'bob'")
    bob = (await cur.fetchone())[0]
    other = await add_share(worker_conn, bob, "다른 협력사", [outside[10], inside[4]])

    seen_by_s = await list_documents(visibility_conn, user_id=share_principal(share_id))
    seen_by_other = await list_documents(visibility_conn, user_id=share_principal(other))

    assert outside[10] not in {row["id"] for row in seen_by_s}
    assert {row["id"] for row in seen_by_other} == {outside[10], inside[4]}


@pytest.mark.parametrize(
    "principal", [f"{SHARE_PRINCIPAL_PREFIX}{uuid4()}", f"{SHARE_PRINCIPAL_PREFIX}not-a-uuid"]
)
async def test_an_unknown_share_sees_nothing(visibility_conn, shared, principal):
    assert await list_documents(visibility_conn, user_id=principal) == []


@pytest.mark.parametrize(
    ("user_id", "sees_s3", "sees_s4"),
    [("alice", True, False), ("bob", False, True), ("carol", False, False), (None, False, False)],
)
async def test_a_share_grant_gives_users_nothing(
    visibility_conn, shared, user_id, sees_s3, sees_s4
):
    """공유 3(앨리스 제한)·공유 4(밥 제한)는 공유 S에만 부여됐다 — 사용자는 소유자만 본다."""
    _, _, inside, _, _ = shared

    listed = {row["id"] for row in await list_documents(visibility_conn, user_id=user_id)}

    assert (inside[3] in listed) is sees_s3
    assert (inside[4] in listed) is sees_s4
    # 조직 공개 문서는 공유와 무관하게 그대로 보인다.
    assert inside[0] in listed


async def test_deleting_the_share_hides_everything_at_once(worker_conn, visibility_conn, shared):
    _, share_id, _, _, _ = shared
    principal = share_principal(share_id)
    assert len(await list_documents(visibility_conn, user_id=principal)) == 5

    await worker_conn.execute("DELETE FROM shares WHERE id = %s", (share_id,))

    assert await list_documents(visibility_conn, user_id=principal) == []


@pytest.mark.parametrize("principal_kind", ["share", "user"])
async def test_search_candidates_can_use_hnsw_with_the_share_predicate(
    visibility_conn, shared, principal_kind
):
    provider, share_id, _, _, _ = shared
    principal = share_principal(share_id) if principal_kind == "share" else "alice"
    params = {
        "qvec": to_pgvector_literal(provider.embed([QUERY])[0]),
        "tags": None,
        "ctype": None,
        "user": principal,
        "k": 10,
    }

    async with visibility_conn.transaction():
        await visibility_conn.execute("SET LOCAL random_page_cost = 1.1")
        await visibility_conn.execute("SET LOCAL enable_seqscan = off")
        cur = await visibility_conn.execute(f"EXPLAIN {SEARCH_SQL}", params)
        plan = "\n".join(row[0] for row in await cur.fetchall())

    assert "idx_chunks_embedding" in plan, f"HNSW 인덱스를 타지 않았다:\n{plan}"
