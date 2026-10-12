"""정리 추천 — 저장된 관계 이웃이 가장 많이 든 폴더를 제안한다 (ADR-064).

이웃은 `document_edges`의 src ∪ dst이고, 열람 술어를 통과한 문서만 센다. 표는 볼 수 있는 폴더에
든 이웃만 내고 분모도 그들이다 — 폴더 없는 이웃은 어느 폴더의 근거도 아니다. 그 절반 이상이자
두 표 이상이 한 폴더에 있어야 제안한다.
"""

import psycopg
import pytest
from conftest import insert_test_document, login_as

from openarchive.services import organize
from openarchive.services.documents import DocumentNotFound
from openarchive.services.organize import (
    SUGGESTION_MIN_SHARE,
    SUGGESTION_MIN_VOTES,
    list_unfiled,
    suggest_folder,
)


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as connection:
        yield connection


async def add_folder(conn, name, *, parent=None, created_by="alice", visibility="public"):
    cur = await conn.execute(
        "INSERT INTO folders (parent_id, name, created_by, visibility) VALUES (%s, %s, %s, %s) RETURNING id",
        (parent, name, created_by, None if parent is not None else visibility),
    )
    return (await cur.fetchone())[0]


async def add_doc(conn, title, *, folder=None, owner="alice", visibility="public", follows=True):
    document_id = await insert_test_document(
        conn, title=title, content=f"{title} 본문", owner_id=owner, visibility=visibility
    )
    await conn.execute(
        "UPDATE documents SET folder_id = %s, follows_folder = %s WHERE id = %s",
        (folder, follows, document_id),
    )
    return document_id


async def link(conn, src, dst, score=0.8):
    await conn.execute(
        """INSERT INTO document_edges
           (src_document_id, dst_document_id, kind, src_chunk_index, dst_chunk_index, score)
           VALUES (%s, %s, 'related', 0, 0, %s)""",
        (src, dst, score),
    )


def test_the_measured_threshold_is_a_simple_majority_of_at_least_two():
    # notes/demo-data-20261012: 한 건씩 가리면 86건 중 53건 제안·73% 정답, 절반을 동시에 가려도 64%.
    # 최소 2표가 없으면 절반 동시 업로드에서 폴더 있는 이웃 하나로 제안해 58%로 떨어졌다.
    assert SUGGESTION_MIN_SHARE == 0.5
    assert SUGGESTION_MIN_VOTES == 2


async def test_suggests_the_folder_holding_most_neighbours_with_its_path(conn):
    root = await add_folder(conn, "경영지원")
    hr = await add_folder(conn, "인사", parent=root)
    finance = await add_folder(conn, "재무", parent=root)
    new = await add_doc(conn, "새 규정")
    for index in range(3):
        await link(conn, new, await add_doc(conn, f"인사 {index}", folder=hr))
    await link(conn, new, await add_doc(conn, "재무 0", folder=finance))

    suggestion = await suggest_folder(conn, document_id=new, user_id="alice")

    assert suggestion is not None
    assert suggestion.folder_id == hr
    assert [item["name"] for item in suggestion.path] == ["경영지원", "인사"]
    assert (suggestion.votes, suggestion.total) == (3, 4)


async def test_exactly_half_is_enough_and_less_than_half_is_not(conn):
    hr = await add_folder(conn, "인사")
    finance = await add_folder(conn, "재무")
    audit = await add_folder(conn, "감사")
    half = await add_doc(conn, "절반")
    split = await add_doc(conn, "갈림")
    for folder in (hr, hr, finance, audit):
        await link(conn, half, await add_doc(conn, "이웃", folder=folder))
    for folder in (hr, hr, finance, finance, audit):
        await link(conn, split, await add_doc(conn, "이웃", folder=folder))

    assert (await suggest_folder(conn, document_id=half, user_id="alice")).folder_id == hr
    assert await suggest_folder(conn, document_id=split, user_id="alice") is None


async def test_neighbours_without_a_folder_are_not_evidence_either_way(conn):
    # 함께 올린 새 문서끼리는 서로 이웃이다 — 그들이 분모에 들면 일괄 업로드에서 제안이 사라진다.
    hr = await add_folder(conn, "인사")
    finance = await add_folder(conn, "재무")
    new = await add_doc(conn, "새 문서")
    await link(conn, new, await add_doc(conn, "인사 1", folder=hr))
    await link(conn, new, await add_doc(conn, "인사 2", folder=hr))
    await link(conn, new, await add_doc(conn, "재무", folder=finance))
    for index in range(4):
        await link(conn, new, await add_doc(conn, f"폴더 없음 {index}"))

    suggestion = await suggest_folder(conn, document_id=new, user_id="alice")

    assert (suggestion.folder_id, suggestion.votes, suggestion.total) == (hr, 2, 3)


async def test_a_single_filed_neighbour_is_not_enough(conn):
    hr = await add_folder(conn, "인사")
    new = await add_doc(conn, "새 문서")
    await link(conn, new, await add_doc(conn, "인사", folder=hr))
    await link(conn, new, await add_doc(conn, "폴더 없음"))

    assert await suggest_folder(conn, document_id=new, user_id="alice") is None


async def test_reverse_edges_count_and_a_pair_stored_both_ways_counts_once(conn):
    hr = await add_folder(conn, "인사")
    finance = await add_folder(conn, "재무")
    new = await add_doc(conn, "새 문서")
    both = await add_doc(conn, "양방향", folder=hr)
    await link(conn, new, both)
    await link(conn, both, new)
    await link(conn, await add_doc(conn, "역방향", folder=hr), new)
    await link(conn, new, await add_doc(conn, "재무", folder=finance))

    suggestion = await suggest_folder(conn, document_id=new, user_id="alice")

    assert suggestion.folder_id == hr
    assert (suggestion.votes, suggestion.total) == (2, 3)


async def test_ties_go_to_the_folder_with_the_higher_total_score(conn):
    hr = await add_folder(conn, "인사")
    finance = await add_folder(conn, "재무")
    new = await add_doc(conn, "새 문서")
    for index in range(2):
        await link(conn, new, await add_doc(conn, f"인사 {index}", folder=hr), score=0.6)
        await link(conn, new, await add_doc(conn, f"재무 {index}", folder=finance), score=0.9)

    assert (await suggest_folder(conn, document_id=new, user_id="alice")).folder_id == finance


async def test_no_suggestion_when_the_majority_is_the_current_folder(conn):
    hr = await add_folder(conn, "인사")
    filed = await add_doc(conn, "이미 정리됨", folder=hr)
    for index in range(2):
        await link(conn, filed, await add_doc(conn, f"인사 이웃 {index}", folder=hr))

    assert await suggest_folder(conn, document_id=filed, user_id="alice") is None


async def test_a_document_already_in_a_folder_gets_a_different_majority_folder(conn):
    hr = await add_folder(conn, "인사")
    finance = await add_folder(conn, "재무")
    misfiled = await add_doc(conn, "잘못 둔 문서", folder=hr)
    await link(conn, misfiled, await add_doc(conn, "재무 1", folder=finance))
    await link(conn, misfiled, await add_doc(conn, "재무 2", folder=finance))

    assert (await suggest_folder(conn, document_id=misfiled, user_id="alice")).folder_id == finance


async def test_no_edges_means_no_suggestion(conn):
    lonely = await add_doc(conn, "외톨이")

    assert await suggest_folder(conn, document_id=lonely, user_id="alice") is None


async def test_invisible_and_trashed_neighbours_are_not_counted(conn):
    hr = await add_folder(conn, "인사")
    secret = await add_folder(conn, "비밀", created_by="bob", visibility="private")
    new = await add_doc(conn, "새 문서")
    for index in range(2):
        await link(conn, new, await add_doc(conn, f"인사 {index}", folder=hr))
    for index in range(3):
        await link(conn, new, await add_doc(conn, f"남의 비밀 {index}", folder=secret, owner="bob"))
    for index in range(2):
        trashed = await add_doc(conn, f"버린 문서 {index}", folder=hr)
        await conn.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (trashed,))
        await link(conn, new, trashed)

    alice = await suggest_folder(conn, document_id=new, user_id="alice")
    bob = await suggest_folder(conn, document_id=new, user_id="bob")

    assert (alice.folder_id, alice.votes, alice.total) == (hr, 2, 2)
    assert (bob.folder_id, bob.votes, bob.total) == (secret, 3, 5)


async def test_a_visible_neighbour_in_an_invisible_folder_never_names_that_folder(conn):
    # 「개별 지정」 공개 문서는 볼 수 있어도 그 폴더는 볼 수 없다 — 폴더 이름이 새면 안 된다.
    secret = await add_folder(conn, "비밀", created_by="bob", visibility="private")
    new = await add_doc(conn, "새 문서")
    hr = await add_folder(conn, "인사")
    for index in range(2):
        await link(conn, new, await add_doc(
            conn, f"공개 {index}", folder=secret, owner="bob", visibility="public", follows=False
        ))
    for index in range(2):
        await link(conn, new, await add_doc(conn, f"인사 {index}", folder=hr))

    # alice에게 비밀 폴더 이웃은 폴더 없는 이웃과 같다 — 표도 분모도 아니다.
    alice = await suggest_folder(conn, document_id=new, user_id="alice")
    assert (alice.folder_id, alice.votes, alice.total) == (hr, 2, 2)
    bob = await suggest_folder(conn, document_id=new, user_id="bob")
    assert (bob.votes, bob.total) == (2, 4)


async def test_an_individually_private_document_in_a_visible_folder_is_not_counted(conn):
    # 보이는 폴더 안이라도 「개별 지정」 비공개인 남의 문서는 표도 분모도 아니다 — 수가 새면 존재가 샌다.
    hr = await add_folder(conn, "인사")
    finance = await add_folder(conn, "재무")
    new = await add_doc(conn, "새 문서")
    for index in range(2):
        await link(conn, new, await add_doc(conn, f"인사 {index}", folder=hr))
    for index in range(3):
        await link(conn, new, await add_doc(
            conn, f"남의 비공개 {index}", folder=finance, owner="bob", visibility="private", follows=False
        ))

    alice = await suggest_folder(conn, document_id=new, user_id="alice")

    assert (alice.folder_id, alice.votes, alice.total) == (hr, 2, 2)


async def test_the_subject_document_must_be_visible(conn):
    private = await add_doc(conn, "남의 문서", owner="bob", visibility="private")

    with pytest.raises(DocumentNotFound):
        await suggest_folder(conn, document_id=private, user_id="alice")


async def test_unfiled_lists_only_my_documents_without_a_folder(conn):
    hr = await add_folder(conn, "인사")
    mine = await add_doc(conn, "내 새 문서")
    lonely = await add_doc(conn, "내 외톨이")
    await add_doc(conn, "정리된 내 문서", folder=hr)
    await add_doc(conn, "남의 공개 문서", owner="bob")
    trashed = await add_doc(conn, "버린 내 문서")
    await conn.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (trashed,))
    for index in range(2):
        await link(conn, mine, await add_doc(conn, f"인사 이웃 {index}", folder=hr))

    result = await list_unfiled(conn, user_id="alice")

    by_title = {item.title: item for item in result.items}
    assert set(by_title) == {"내 새 문서", "내 외톨이"}
    assert result.count == 2
    assert by_title["내 새 문서"].suggestion.folder_id == hr
    assert by_title["내 새 문서"].document_id == mine
    assert by_title["내 외톨이"].suggestion is None
    assert by_title["내 외톨이"].document_id == lonely


async def test_unfiled_counts_every_unfiled_document_beyond_the_page(conn, monkeypatch):
    monkeypatch.setattr(organize, "UNFILED_LIMIT", 1)
    await add_doc(conn, "오래된 문서")
    await add_doc(conn, "새 문서")

    result = await list_unfiled(conn, user_id="alice")

    assert result.count == 2
    assert len(result.items) == 1


def _seed_api(dsn):
    with psycopg.connect(dsn, autocommit=True) as sync:
        folder_id = sync.execute(
            "INSERT INTO folders (name, created_by, visibility) VALUES ('인사', 'alice', 'public') RETURNING id"
        ).fetchone()[0]
        ids = []
        for title, folder in (("새 문서", None), ("인사 이웃 1", folder_id), ("인사 이웃 2", folder_id)):
            ids.append(sync.execute(
                """INSERT INTO documents (title, content_type, content, content_hash, owner_id, visibility, folder_id)
                   VALUES (%s, 'md', %s, md5(%s), 'alice', 'public', %s) RETURNING id""",
                (title, title, title, folder),
            ).fetchone()[0])
        for neighbour in ids[1:]:
            sync.execute(
                """INSERT INTO document_edges
                   (src_document_id, dst_document_id, kind, src_chunk_index, dst_chunk_index, score)
                   VALUES (%s, %s, 'related', 0, 0, 0.8)""",
                (ids[0], neighbour),
            )
    return folder_id, ids[0]


def test_folder_suggestion_api(db_client, migrated_db):
    folder_id, document_id = _seed_api(migrated_db)
    login_as(db_client, "alice")

    response = db_client.get(f"/api/documents/{document_id}/folder-suggestion")

    assert response.status_code == 200
    assert response.json() == {
        "suggestion": {
            "folder_id": str(folder_id),
            "path": [{"id": str(folder_id), "name": "인사"}],
            "votes": 2,
            "total": 2,
        }
    }


def test_unfiled_api(db_client, migrated_db):
    folder_id, document_id = _seed_api(migrated_db)
    login_as(db_client, "alice")

    response = db_client.get("/api/documents/unfiled")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert [item["document_id"] for item in body["items"]] == [str(document_id)]
    assert body["items"][0]["title"] == "새 문서"
    assert body["items"][0]["suggestion"]["folder_id"] == str(folder_id)


def test_organize_apis_require_a_user(db_client, migrated_db):
    _, document_id = _seed_api(migrated_db)

    assert db_client.get(f"/api/documents/{document_id}/folder-suggestion").status_code == 401
    assert db_client.get("/api/documents/unfiled").status_code == 401
