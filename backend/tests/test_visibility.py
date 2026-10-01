from uuid import uuid4

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from openarchive.embeddings import FakeProvider
from openarchive.services.auth import hash_password
from openarchive.services.clusters import get_clusters
from openarchive.services.diagnostics import get_diagnostics
from openarchive.services.documents import (
    DocumentAccessDenied,
    DocumentNotFound,
    get_document,
    list_documents,
    update_tags,
)
from openarchive.services.links import find_backlinks, resolve_links
from openarchive.services.related import find_related, suggest_tags
from openarchive.services.search import search_documents


@pytest.fixture
async def worker_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        yield conn


@pytest.fixture
async def visibility_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        yield conn


@pytest.fixture
async def visible_documents(worker_conn):
    provider = FakeProvider()
    content = "OpenSQL 권한 경계와 문서 관계"
    public_id = await insert_test_document(
        worker_conn,
        title="공개 문서",
        content=content,
        owner_id="alice",
        visibility="public",
    )
    alice_private_id = await insert_test_document(
        worker_conn,
        title="앨리스 비공개 문서",
        content=content,
        owner_id="alice",
        visibility="private",
        tags=["alice-secret"],
    )
    bob_private_id = await insert_test_document(
        worker_conn,
        title="밥 비공개 문서",
        content="OpenSQL 권한 경계와 비공개 운영",
        owner_id="bob",
        visibility="private",
    )
    await process_all_embedding_jobs(worker_conn, provider)
    return provider, public_id, alice_private_id, bob_private_id


async def test_anonymous_search_hides_private_documents(
    visibility_conn, visible_documents
):
    provider, public_id, alice_private_id, _ = visible_documents

    hits = await search_documents(
        visibility_conn, provider, query="OpenSQL 권한 경계", user_id=None
    )

    assert [hit.document_id for hit in hits] == [public_id]
    assert alice_private_id not in {hit.document_id for hit in hits}


async def test_other_user_search_hides_alice_private_document(
    visibility_conn, visible_documents
):
    provider, public_id, alice_private_id, bob_private_id = visible_documents

    hits = await search_documents(
        visibility_conn, provider, query="OpenSQL 권한 경계", user_id="bob"
    )

    assert {hit.document_id for hit in hits} == {public_id, bob_private_id}
    assert len(hits) == 2
    assert alice_private_id not in {hit.document_id for hit in hits}


async def test_owner_search_includes_own_private_document(
    visibility_conn, visible_documents
):
    provider, public_id, alice_private_id, _ = visible_documents

    hits = await search_documents(
        visibility_conn, provider, query="OpenSQL 권한 경계", user_id="alice"
    )

    assert {hit.document_id for hit in hits} == {public_id, alice_private_id}
    assert len(hits) == 2


async def test_related_hides_the_existence_of_an_invisible_subject_document(
    visibility_conn, visible_documents
):
    _, _, _, bob_private_id = visible_documents

    for user_id in (None, "alice"):
        with pytest.raises(DocumentNotFound):
            await find_related(
                visibility_conn, document_id=bob_private_id, user_id=user_id
            )

    owner = await find_related(
        visibility_conn, document_id=bob_private_id, user_id="bob"
    )
    assert owner.based_on_version is not None

    with pytest.raises(DocumentNotFound):
        await find_related(visibility_conn, document_id=uuid4(), user_id="bob")


async def test_tag_suggestions_hide_the_existence_of_an_invisible_subject_document(
    visibility_conn, visible_documents
):
    _, _, _, bob_private_id = visible_documents

    for user_id in (None, "alice"):
        with pytest.raises(DocumentNotFound):
            await suggest_tags(
                visibility_conn, document_id=bob_private_id, user_id=user_id
            )

    owner = await suggest_tags(
        visibility_conn, document_id=bob_private_id, user_id="bob"
    )
    assert owner.based_on_version is not None

    with pytest.raises(DocumentNotFound):
        await suggest_tags(visibility_conn, document_id=uuid4(), user_id="bob")


async def test_link_resolution_hides_the_existence_of_an_invisible_subject_document(
    visibility_conn, visible_documents
):
    _, _, _, bob_private_id = visible_documents

    for user_id in (None, "alice"):
        with pytest.raises(DocumentNotFound):
            await resolve_links(
                visibility_conn, document_id=bob_private_id, user_id=user_id
            )

    assert await resolve_links(
        visibility_conn, document_id=bob_private_id, user_id="bob"
    ) == []

    with pytest.raises(DocumentNotFound):
        await resolve_links(visibility_conn, document_id=uuid4(), user_id="bob")


async def test_backlinks_hide_the_existence_of_an_invisible_subject_document(
    visibility_conn, visible_documents
):
    _, _, _, bob_private_id = visible_documents

    for user_id in (None, "alice"):
        with pytest.raises(DocumentNotFound):
            await find_backlinks(
                visibility_conn, document_id=bob_private_id, user_id=user_id
            )

    assert await find_backlinks(
        visibility_conn, document_id=bob_private_id, user_id="bob"
    ) == []

    with pytest.raises(DocumentNotFound):
        await find_backlinks(visibility_conn, document_id=uuid4(), user_id="bob")


async def test_admin_search_still_hides_other_users_private_documents(
    worker_conn, visibility_conn, visible_documents
):
    provider, public_id, alice_private_id, bob_private_id = visible_documents
    await worker_conn.execute(
        "INSERT INTO users (username, password_hash, is_admin) VALUES ('admin', %s, true)",
        (hash_password("admin-secret"),),
    )

    hits = await search_documents(
        visibility_conn, provider, query="OpenSQL 권한 경계", user_id="admin"
    )

    assert [hit.document_id for hit in hits] == [public_id]
    assert alice_private_id not in {hit.document_id for hit in hits}
    assert bob_private_id not in {hit.document_id for hit in hits}


async def test_private_and_missing_wikilinks_are_indistinguishable_to_anonymous_users(
    worker_conn, visibility_conn
):
    source_id = await insert_test_document(
        worker_conn,
        title="링크 출발",
        content="[[숨은 대상]]과 [[없는 대상]]",
    )
    await insert_test_document(
        worker_conn,
        title="숨은 대상",
        content="비공개 링크 대상",
        owner_id="alice",
        visibility="private",
    )

    links = await resolve_links(visibility_conn, document_id=source_id, user_id=None)
    other_user_links = await resolve_links(
        visibility_conn, document_id=source_id, user_id="bob"
    )

    assert [(link.title, link.document_id) for link in links] == [
        ("숨은 대상", None),
        ("없는 대상", None),
    ]
    assert other_user_links == links
    assert all(set(link.__dict__) == {"title", "document_id"} for link in links)


async def test_wikilink_resolution_returns_every_visible_duplicate_title(
    worker_conn, visibility_conn
):
    source_id = await insert_test_document(
        worker_conn, title="동명 링크 출발", content="[[같은 제목]]"
    )
    public_id = await insert_test_document(
        worker_conn, title="같은 제목", content="공개 동명 문서"
    )
    private_id = await insert_test_document(
        worker_conn,
        title="같은 제목",
        content="비공개 동명 문서",
        owner_id="alice",
        visibility="private",
    )

    anonymous = await resolve_links(
        visibility_conn, document_id=source_id, user_id=None
    )
    owner = await resolve_links(
        visibility_conn, document_id=source_id, user_id="alice"
    )

    assert [link.document_id for link in anonymous] == [public_id]
    assert {link.document_id for link in owner} == {public_id, private_id}


async def test_backlinks_include_only_visible_source_documents(
    worker_conn, visibility_conn
):
    target_id = await insert_test_document(
        worker_conn, title="백링크 대상", content="대상 문서"
    )
    public_source_id = await insert_test_document(
        worker_conn, title="공개 출발", content="[[백링크 대상]]"
    )
    private_source_id = await insert_test_document(
        worker_conn,
        title="비공개 출발",
        content="[[백링크 대상]]",
        owner_id="alice",
        visibility="private",
    )

    anonymous = await find_backlinks(
        visibility_conn, document_id=target_id, user_id=None
    )
    other_user = await find_backlinks(
        visibility_conn, document_id=target_id, user_id="bob"
    )
    owner = await find_backlinks(
        visibility_conn, document_id=target_id, user_id="alice"
    )

    assert [link.document_id for link in anonymous] == [public_source_id]
    assert other_user == anonymous
    assert {link.document_id for link in owner} == {
        public_source_id,
        private_source_id,
    }


@pytest.mark.parametrize(
    ("user_id", "can_traverse_through_private"),
    [(None, False), ("bob", False), ("alice", True)],
)
async def test_graph_search_cannot_traverse_through_an_invisible_private_document(
    worker_conn, visibility_conn, user_id, can_traverse_through_private
):
    provider = FakeProvider()
    entry_id = await insert_test_document(
        worker_conn,
        title="공개 진입점",
        content=("그래프 경유 차단 질의 " * 2000),
    )
    private_id = await insert_test_document(
        worker_conn,
        title="비공개 중간 노드",
        content="중간 비공개 문서",
        owner_id="alice",
        visibility="private",
    )
    beyond_id = await insert_test_document(
        worker_conn,
        title="비공개 너머 공개 노드",
        content="너머 공개 문서",
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")
    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind,
             src_chunk_index, dst_chunk_index, score)
        VALUES (%s, %s, 'related', 0, 0, 0.9),
               (%s, %s, 'related', 0, 0, 0.8)
        """,
        (entry_id, private_id, private_id, beyond_id),
    )

    hits = await search_documents(
        visibility_conn,
        provider,
        query="그래프 경유 차단 질의",
        user_id=user_id,
        k=3,
    )
    ids = {hit.document_id for hit in hits}

    assert (private_id in ids) is can_traverse_through_private
    assert (beyond_id in ids) is can_traverse_through_private


@pytest.mark.parametrize(
    ("user_id", "can_follow_private_link"),
    [(None, False), ("bob", False), ("alice", True)],
)
async def test_graph_search_resolves_wikilinks_without_crossing_visibility(
    worker_conn, visibility_conn, user_id, can_follow_private_link
):
    provider = FakeProvider()
    entry_id = await insert_test_document(
        worker_conn,
        title="위키링크 진입점",
        content=("위키링크 순회 질의 " * 2000) + "[[숨은 링크 대상]]",
    )
    private_id = await insert_test_document(
        worker_conn,
        title="숨은 링크 대상",
        content="위키링크가 가리키는 비공개 문서",
        owner_id="alice",
        visibility="private",
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")

    hits = await search_documents(
        visibility_conn,
        provider,
        query="위키링크 순회 질의",
        user_id=user_id,
        k=3,
    )
    private_hits = [hit for hit in hits if hit.document_id == private_id]

    assert bool(private_hits) is can_follow_private_link
    if private_hits:
        assert private_hits[0].via is not None
        assert private_hits[0].via.kind == "refers"
        assert private_hits[0].via.from_document_id == entry_id


async def test_anonymous_related_hides_private_documents(
    visibility_conn, visible_documents
):
    _, public_id, alice_private_id, _ = visible_documents

    result = await find_related(
        visibility_conn, document_id=public_id, user_id=None
    )

    assert result.items == []
    assert alice_private_id not in {item.document_id for item in result.items}


async def test_other_user_related_hides_alice_private_document(
    visibility_conn, visible_documents
):
    _, public_id, alice_private_id, bob_private_id = visible_documents

    result = await find_related(
        visibility_conn, document_id=public_id, user_id="bob"
    )

    assert [item.document_id for item in result.items] == [bob_private_id]
    assert len(result.items) == 1
    assert alice_private_id not in {item.document_id for item in result.items}


async def test_owner_related_includes_own_private_document(
    visibility_conn, visible_documents
):
    _, public_id, alice_private_id, _ = visible_documents

    result = await find_related(
        visibility_conn, document_id=public_id, user_id="alice"
    )

    assert [item.document_id for item in result.items] == [alice_private_id]
    assert len(result.items) == 1


async def test_tag_suggestions_do_not_leave_private_placeholders(
    visibility_conn, visible_documents
):
    _, public_id, _, _ = visible_documents

    anonymous = await suggest_tags(
        visibility_conn, document_id=public_id, user_id=None
    )
    other_user = await suggest_tags(
        visibility_conn, document_id=public_id, user_id="bob"
    )
    owner = await suggest_tags(
        visibility_conn, document_id=public_id, user_id="alice"
    )

    assert anonymous.items == []
    assert other_user.items == []
    assert [(item.tag, item.freq) for item in owner.items] == [("alice-secret", 1)]
    assert len(owner.items) == 1


async def test_identical_documents_do_not_leave_private_placeholders(
    visibility_conn, visible_documents
):
    _, public_id, alice_private_id, _ = visible_documents

    anonymous = await find_related(
        visibility_conn, document_id=public_id, user_id=None
    )
    other_user = await find_related(
        visibility_conn, document_id=public_id, user_id="bob"
    )
    owner = await find_related(
        visibility_conn, document_id=public_id, user_id="alice"
    )

    assert anonymous.identical == []
    assert other_user.identical == []
    assert [item.document_id for item in owner.identical] == [alice_private_id]
    assert len(owner.identical) == 1


async def test_diagnostics_counts_follow_anonymous_other_and_owner_visibility(
    worker_conn, visibility_conn
):
    await insert_test_document(
        worker_conn, title="공개 미분류", content="공개", tags=[]
    )
    await insert_test_document(
        worker_conn,
        title="앨리스 미분류",
        content="앨리스",
        owner_id="alice",
        visibility="private",
        tags=[],
    )
    await insert_test_document(
        worker_conn,
        title="밥 미분류 1",
        content="밥 하나",
        owner_id="bob",
        visibility="private",
        tags=[],
    )
    await insert_test_document(
        worker_conn,
        title="밥 미분류 2",
        content="밥 둘",
        owner_id="bob",
        visibility="private",
        tags=[],
    )

    anonymous = await get_diagnostics(visibility_conn, user_id=None)
    other = await get_diagnostics(visibility_conn, user_id="bob")
    owner = await get_diagnostics(visibility_conn, user_id="alice")

    assert anonymous.uncategorized.count == 1
    assert other.uncategorized.count == 3
    assert owner.uncategorized.count == 2


async def test_duplicate_diagnostics_hide_pairs_and_totals_unless_both_ends_are_visible(
    worker_conn, visibility_conn
):
    provider = FakeProvider()
    identical_content = "동일한 추출 텍스트"
    await insert_test_document(
        worker_conn,
        title="공개 동일 문서",
        content=identical_content,
        owner_id="alice",
        visibility="public",
    )
    await insert_test_document(
        worker_conn,
        title="비공개 동일 문서",
        content=identical_content,
        owner_id="alice",
        visibility="private",
    )
    overlap_public_id = await insert_test_document(
        worker_conn,
        title="공개 겹침 문서",
        content="OpenSQL 관계 진단 공개 문서 " * 100,
        owner_id="alice",
        visibility="public",
    )
    overlap_private_id = await insert_test_document(
        worker_conn,
        title="비공개 겹침 문서",
        content="OpenSQL 관계 진단 비공개 문서 " * 100,
        owner_id="alice",
        visibility="private",
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")
    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind, score)
        VALUES (%s, %s, 'overlaps', 0.97)
        """,
        (overlap_public_id, overlap_private_id),
    )

    anonymous = await get_diagnostics(visibility_conn, user_id=None)
    other = await get_diagnostics(visibility_conn, user_id="bob")
    owner = await get_diagnostics(visibility_conn, user_id="alice")

    for hidden in (anonymous, other):
        assert hidden.duplicates.identical.count == 0
        assert hidden.duplicates.identical.items == []
        assert hidden.duplicates.overlaps.count == 0
        assert hidden.duplicates.overlaps.items == []

    assert owner.duplicates.identical.count == 1
    assert len(owner.duplicates.identical.items) == 1
    assert owner.duplicates.overlaps.count == 1
    assert len(owner.duplicates.overlaps.items) == 1


async def test_broken_link_diagnostics_follow_the_viewers_visibility(
    worker_conn, visibility_conn
):
    await insert_test_document(
        worker_conn,
        title="링크 진단 출발",
        content="[[비공개 대상]]과 [[없는 대상]]",
    )
    await insert_test_document(
        worker_conn,
        title="비공개 대상",
        content="소유자만 보는 대상",
        owner_id="alice",
        visibility="private",
    )

    anonymous = await get_diagnostics(visibility_conn, user_id=None)
    owner = await get_diagnostics(visibility_conn, user_id="alice")

    assert anonymous.broken_links.count == 2
    assert {item.target_title for item in anonymous.broken_links.items} == {
        "비공개 대상",
        "없는 대상",
    }
    assert owner.broken_links.count == 1
    assert [item.target_title for item in owner.broken_links.items] == ["없는 대상"]


async def test_cluster_sizes_follow_anonymous_other_and_owner_visibility(
    worker_conn, visibility_conn
):
    public_id = await insert_test_document(
        worker_conn, title="공개 검색", content="공개 검색", tags=["공개"]
    )
    alice_id = await insert_test_document(
        worker_conn,
        title="앨리스 검색",
        content="앨리스 검색",
        owner_id="alice",
        visibility="private",
        tags=["앨리스"],
    )
    bob_id = await insert_test_document(
        worker_conn,
        title="밥 검색 1",
        content="밥 검색 하나",
        owner_id="bob",
        visibility="private",
        tags=["밥"],
    )
    await insert_test_document(
        worker_conn,
        title="밥 검색 2",
        content="밥 검색 둘",
        owner_id="bob",
        visibility="private",
        tags=["밥"],
    )
    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind, score)
        VALUES (%s, %s, 'related', 0.8),
               (%s, %s, 'related', 0.8)
        """,
        (public_id, alice_id, public_id, bob_id),
    )

    anonymous = await get_clusters(visibility_conn, user_id=None)
    other = await get_clusters(visibility_conn, user_id="bob")
    owner = await get_clusters(visibility_conn, user_id="alice")

    assert anonymous.clusters[0].size == 1
    assert sorted(cluster.size for cluster in other.clusters) == [1, 2]
    assert sorted(cluster.size for cluster in owner.clusters) == [2]
    assert anonymous.connections == []
    assert other.connections == []
    assert owner.connections == []


# ── 부여 (ADR-044 결정 2, #97) ───────────────────────────────────────────
# private 문서는 소유자와 부여 대상이 본다. carol은 직접 부여, dave는 부여된 그룹의
# 구성원, erin은 부여 없는 다른 그룹의 구성원이다. 익명은 어떤 부여에도 해당하지 않는다.

VIEWERS = [("carol", True), ("dave", True), ("erin", False), (None, False)]


async def add_user(conn, username: str):
    cur = await conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, 'scrypt-hash') RETURNING id",
        (username,),
    )
    return (await cur.fetchone())[0]


@pytest.fixture
async def granted(worker_conn):
    """앨리스의 private 문서 하나를 carol과 인사팀에 부여한다."""
    provider = FakeProvider()
    content = "OpenSQL 부여 경계와 문서 관계"
    public_id = await insert_test_document(
        worker_conn, title="공개 출발", content=content + " [[부여 대상]]"
    )
    granted_id = await insert_test_document(
        worker_conn,
        title="부여 대상",
        content=content + " [[공개 출발]]",
        owner_id="alice",
        visibility="private",
        tags=["granted-secret"],
    )
    carol, dave, erin = [await add_user(worker_conn, u) for u in ("carol", "dave", "erin")]
    cur = await worker_conn.execute(
        "INSERT INTO groups (name) VALUES ('인사팀'), ('재무팀') RETURNING id"
    )
    hr, finance = [row[0] for row in await cur.fetchall()]
    await worker_conn.execute(
        "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s), (%s, %s)",
        (hr, dave, finance, erin),
    )
    await worker_conn.execute(
        "INSERT INTO document_grants (document_id, user_id) VALUES (%s, %s)",
        (granted_id, carol),
    )
    await worker_conn.execute(
        "INSERT INTO document_grants (document_id, group_id) VALUES (%s, %s)",
        (granted_id, hr),
    )
    await process_all_embedding_jobs(worker_conn, provider)
    return provider, public_id, granted_id


@pytest.mark.parametrize(("user_id", "sees"), VIEWERS)
async def test_search_follows_grants(visibility_conn, granted, user_id, sees):
    provider, public_id, granted_id = granted

    hits = await search_documents(
        visibility_conn, provider, query="OpenSQL 부여 경계", user_id=user_id
    )

    assert public_id in {hit.document_id for hit in hits}
    assert (granted_id in {hit.document_id for hit in hits}) is sees


@pytest.mark.parametrize(("user_id", "sees"), VIEWERS)
async def test_document_reads_follow_grants(visibility_conn, granted, user_id, sees):
    _, _, granted_id = granted

    listed = await list_documents(visibility_conn, user_id=user_id)
    assert (granted_id in {row["id"] for row in listed}) is sees

    if sees:
        document = await get_document(visibility_conn, granted_id, user_id=user_id)
        assert document["id"] == granted_id
    else:
        with pytest.raises(DocumentNotFound):
            await get_document(visibility_conn, granted_id, user_id=user_id)


@pytest.mark.parametrize(("user_id", "sees"), VIEWERS)
async def test_related_and_tag_suggestions_follow_grants(
    visibility_conn, granted, user_id, sees
):
    _, public_id, granted_id = granted

    related = await find_related(visibility_conn, document_id=public_id, user_id=user_id)
    tags = await suggest_tags(visibility_conn, document_id=public_id, user_id=user_id)

    assert (granted_id in {item.document_id for item in related.items}) is sees
    assert ("granted-secret" in {item.tag for item in tags.items}) is sees
    if not sees:
        with pytest.raises(DocumentNotFound):
            await find_related(visibility_conn, document_id=granted_id, user_id=user_id)


@pytest.mark.parametrize(("user_id", "sees"), VIEWERS)
async def test_wikilinks_follow_grants(visibility_conn, granted, user_id, sees):
    _, public_id, granted_id = granted

    links = await resolve_links(visibility_conn, document_id=public_id, user_id=user_id)
    backlinks = await find_backlinks(visibility_conn, document_id=public_id, user_id=user_id)

    assert [link.document_id for link in links] == [granted_id if sees else None]
    assert [link.document_id for link in backlinks] == ([granted_id] if sees else [])


@pytest.mark.parametrize(("user_id", "sees"), VIEWERS)
async def test_aggregates_follow_grants(visibility_conn, granted, user_id, sees):
    diagnostics = await get_diagnostics(visibility_conn, user_id=user_id)
    clusters = await get_clusters(visibility_conn, user_id=user_id)

    # 공개 출발 문서는 태그가 없다. 부여 대상은 태그가 있어 미분류에 들지 않는다.
    assert diagnostics.uncategorized.count == 1
    # 부여 대상이 안 보이면 공개 출발의 [[부여 대상]]은 깨진 링크로 보인다.
    assert diagnostics.broken_links.count == (0 if sees else 1)
    assert sum(cluster.size for cluster in clusters.clusters) == (2 if sees else 1)


async def test_removing_a_member_hides_the_group_grant_at_once(
    worker_conn, visibility_conn, granted
):
    provider, _, granted_id = granted
    before = await search_documents(
        visibility_conn, provider, query="OpenSQL 부여 경계", user_id="dave"
    )
    assert granted_id in {hit.document_id for hit in before}

    await worker_conn.execute(
        "DELETE FROM group_members WHERE user_id = (SELECT id FROM users WHERE username = 'dave')"
    )

    after = await search_documents(
        visibility_conn, provider, query="OpenSQL 부여 경계", user_id="dave"
    )

    assert granted_id not in {hit.document_id for hit in after}


@pytest.mark.parametrize(
    ("user_id", "error"),
    [("carol", DocumentAccessDenied), ("dave", DocumentAccessDenied), ("erin", DocumentNotFound)],
)
async def test_a_grant_is_read_only(visibility_conn, granted, user_id, error):
    """부여는 읽기다 — 보이는 사람은 쓰기를 403으로, 안 보이는 사람은 404로 거절받는다."""
    _, _, granted_id = granted

    with pytest.raises(error):
        await update_tags(visibility_conn, granted_id, user_id=user_id, tags=["x"])
