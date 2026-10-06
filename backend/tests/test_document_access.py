"""문서 열람 범위 조회·교체와 생성 시 부여 대상 (ADR-044 관리 경로)."""

import psycopg
import pytest
from conftest import insert_test_document

from openarchive.services.documents import (
    DocumentAccessDenied,
    DocumentNotFound,
    GrantsOnPublicDocument,
    GrantToOwner,
    IdempotencyKeyReused,
    InvalidVisibility,
    create_document,
    create_text_document,
    ensure_visible,
    get_access,
    set_access,
)
from openarchive.services.grants import (
    UnknownGrantee,
    add_member,
    create_group,
    insert_grants,
    resolve_grantees,
)
from openarchive.services.shares import add_document, create_share


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as connection:
        for username in ["alice", "bob", "carol", "dave"]:
            await connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, 'unused')", (username,)
            )
        group = await create_group(connection, "인사팀")
        await add_member(connection, group["id"], "dave")
        yield connection


async def grant(conn, document_id, *, users=(), groups=()):
    user_ids, group_ids = await resolve_grantees(conn, users=list(users), groups=list(groups))
    await insert_grants(conn, document_id, user_ids, group_ids)


async def visible(conn, document_id, user_id):
    try:
        await ensure_visible(conn, document_id, user_id=user_id)
    except DocumentNotFound:
        return False
    return True


async def snapshot(conn, document_id):
    cur = await conn.execute("SELECT visibility FROM documents WHERE id = %s", (document_id,))
    visibility = (await cur.fetchone())[0]
    cur = await conn.execute(
        "SELECT user_id, group_id FROM document_grants WHERE document_id = %s ORDER BY 1, 2",
        (document_id,),
    )
    return visibility, await cur.fetchall()


async def document_count(conn):
    cur = await conn.execute("SELECT count(*) FROM documents")
    return (await cur.fetchone())[0]


async def access_audit_details(conn, document_id):
    cur = await conn.execute(
        "SELECT detail FROM audit_log WHERE document_id = %s "
        "AND action = 'access_changed' ORDER BY id", (document_id,),
    )
    return [row[0] for row in await cur.fetchall()]


@pytest.mark.parametrize(
    ("before_users", "after_users", "after_groups", "changes"),
    [
        (["bob"], ["bob"], [], []),
        (["bob"], ["bob", "carol"], [], [("added", "user", "carol")]),
        (["bob", "carol"], ["carol"], [], [("removed", "user", "bob")]),
        (["bob"], [], ["재무팀"],
         [("removed", "user", "bob"), ("added", "group", "재무팀")]),
    ],
)
async def test_set_access_audits_only_grant_differences(
    conn, before_users, after_users, after_groups, changes
):
    await create_group(conn, "재무팀")
    doc = await insert_test_document(conn, title="d", content="본문", visibility="private")
    await grant(conn, doc, users=before_users)
    before = await access_audit_details(conn, doc)
    result = await set_access(
        conn, doc, user_id="alice", visibility="private", users=after_users, groups=after_groups
    )
    assert result == {
        "follows_folder": True,
        "folder": None,
        "folder_scope": None,
        "visibility": "private",
        "users": sorted(after_users),
        "groups": after_groups,
    }
    assert await access_audit_details(conn, doc) == before + [
        {"kind": "grant", "change": change, "grantee_type": kind, "grantee": name}
        for change, kind, name in changes
    ]


async def test_set_access_audits_visibility_and_grants(conn):
    doc = await insert_test_document(conn, title="d", content="본문")
    for visibility, users, previous, change in [
        ("private", ["bob"], "public", "added"),
        ("public", [], "private", "removed"),
    ]:
        before = await access_audit_details(conn, doc)
        await set_access(conn, doc, user_id="alice", visibility=visibility, users=users, groups=[])
        assert await access_audit_details(conn, doc) == before + [
            {"kind": "visibility", "before": previous, "after": visibility},
            {"kind": "grant", "change": change, "grantee_type": "user", "grantee": "bob"},
        ]


async def test_get_access_returns_sorted_names_for_owner(conn):
    doc = await insert_test_document(conn, title="d", content="본문", visibility="private")
    await grant(conn, doc, users=["carol", "bob"], groups=["인사팀"])
    assert await get_access(conn, doc, user_id="alice") == {
        "follows_folder": True,
        "folder": None,
        "folder_scope": None,
        "visibility": "private",
        "users": ["bob", "carol"],
        "groups": ["인사팀"],
    }


async def test_get_access_on_document_without_grants(conn):
    doc = await insert_test_document(conn, title="d", content="본문")
    assert await get_access(conn, doc, user_id="alice") == {
        "follows_folder": True,
        "folder": None,
        "folder_scope": None,
        "visibility": "public",
        "users": [],
        "groups": [],
    }


@pytest.mark.parametrize("operation", ["get", "set"])
async def test_only_owner_reads_or_changes_access(conn, operation):
    public = await insert_test_document(conn, title="p", content="본문")
    private = await insert_test_document(conn, title="r", content="본문", visibility="private")
    await grant(conn, private, users=["bob"])

    async def call(document_id, user_id):
        if operation == "get":
            return await get_access(conn, document_id, user_id=user_id)
        return await set_access(
            conn, document_id, user_id=user_id, visibility="private", users=[], groups=[]
        )

    # 볼 수 있는 비소유자 — 공개 문서의 다른 사용자, 부여받은 사용자
    with pytest.raises(DocumentAccessDenied):
        await call(public, "bob")
    with pytest.raises(DocumentAccessDenied):
        await call(private, "bob")
    # 볼 수 없는 사용자와 익명은 존재를 알 수 없다
    with pytest.raises(DocumentNotFound):
        await call(private, "carol")
    for document_id in (public, private):
        with pytest.raises(DocumentNotFound):
            await call(document_id, None)
    assert await snapshot(conn, public) == ("public", [])


async def test_set_access_replaces_all_grants(conn):
    doc = await insert_test_document(conn, title="d", content="본문", visibility="private")
    await grant(conn, doc, users=["carol"])
    assert await visible(conn, doc, "carol")

    result = await set_access(
        conn, doc, user_id="alice", visibility="private", users=["bob"], groups=["인사팀"]
    )

    assert result == {
        "follows_folder": True,
        "folder": None,
        "folder_scope": None,
        "visibility": "private",
        "users": ["bob"],
        "groups": ["인사팀"],
    }
    assert await get_access(conn, doc, user_id="alice") == result
    assert await visible(conn, doc, "bob")
    assert await visible(conn, doc, "dave")
    assert not await visible(conn, doc, "carol")


async def test_set_access_to_public_clears_grants(conn):
    doc = await insert_test_document(conn, title="d", content="본문", visibility="private")
    await grant(conn, doc, users=["bob"], groups=["인사팀"])

    result = await set_access(conn, doc, user_id="alice", visibility="public", users=[], groups=[])

    assert result == {
        "follows_folder": True,
        "folder": None,
        "folder_scope": None,
        "visibility": "public",
        "users": [],
        "groups": [],
    }
    assert await snapshot(conn, doc) == ("public", [])
    assert await visible(conn, doc, "carol")


async def test_set_access_makes_public_document_private(conn):
    doc = await insert_test_document(conn, title="d", content="본문")
    await set_access(conn, doc, user_id="alice", visibility="private", users=["bob"], groups=[])
    assert await visible(conn, doc, "bob")
    assert not await visible(conn, doc, "carol")


async def test_set_access_rejects_grantees_on_public(conn):
    doc = await insert_test_document(conn, title="d", content="본문", visibility="private")
    await grant(conn, doc, users=["carol"])
    before = await snapshot(conn, doc)
    with pytest.raises(GrantsOnPublicDocument, match="visibility=private"):
        await set_access(conn, doc, user_id="alice", visibility="public", users=["bob"], groups=[])
    with pytest.raises(GrantsOnPublicDocument):
        await set_access(
            conn, doc, user_id="alice", visibility="public", users=[], groups=["인사팀"]
        )
    assert await snapshot(conn, doc) == before


@pytest.mark.parametrize(
    ("users", "groups", "kind"),
    [(["bob", "ghost"], [], "user"), (["bob"], ["유령팀"], "group")],
)
@pytest.mark.parametrize("start", ["public", "private"])
async def test_set_access_with_unknown_name_changes_nothing(conn, users, groups, kind, start):
    doc = await insert_test_document(conn, title="d", content="본문", visibility=start)
    if start == "private":
        await grant(conn, doc, users=["carol"])
    before = await snapshot(conn, doc)
    audit_before = await access_audit_details(conn, doc)
    with pytest.raises(UnknownGrantee) as exc:
        await set_access(
            conn, doc, user_id="alice", visibility="private", users=users, groups=groups
        )
    assert exc.value.kind == kind
    # visibility도 바뀌지 않는다 — 교체는 원자적이다
    assert await snapshot(conn, doc) == before
    assert await access_audit_details(conn, doc) == audit_before


async def test_set_access_rejects_owner_as_grantee(conn):
    """소유자는 이미 본다 — 효력 없는 부여 행을 남기지 않는다 (ADR-044 관리 경로 결정 4)."""
    doc = await insert_test_document(conn, title="d", content="본문", visibility="private")
    await grant(conn, doc, users=["carol"])
    before = await snapshot(conn, doc)
    with pytest.raises(GrantToOwner, match="소유자"):
        await set_access(
            conn, doc, user_id="alice", visibility="private", users=["bob", "alice"], groups=[]
        )
    assert await snapshot(conn, doc) == before


async def test_set_access_rejects_unknown_visibility(conn):
    doc = await insert_test_document(conn, title="d", content="본문")
    with pytest.raises(InvalidVisibility):
        await set_access(conn, doc, user_id="alice", visibility="secret", users=[], groups=[])
    assert await snapshot(conn, doc) == ("public", [])


async def test_set_access_does_not_bump_version_or_create_jobs(conn):
    doc = await insert_test_document(conn, title="d", content="본문")
    cur = await conn.execute("SELECT count(*) FROM embedding_jobs WHERE document_id = %s", (doc,))
    jobs_before = (await cur.fetchone())[0]
    await set_access(conn, doc, user_id="alice", visibility="private", users=["bob"], groups=[])
    cur = await conn.execute("SELECT version FROM documents WHERE id = %s", (doc,))
    assert await cur.fetchone() == (1,)
    cur = await conn.execute("SELECT count(*) FROM embedding_jobs WHERE document_id = %s", (doc,))
    assert (await cur.fetchone())[0] == jobs_before


async def test_create_text_document_with_grantees(conn):
    document = await create_text_document(
        conn,
        title="인사 규정",
        content="본문",
        owner_id="alice",
        visibility="private",
        grant_users=["bob"],
        grant_groups=["인사팀"],
    )
    assert "users" not in document and "groups" not in document
    assert await visible(conn, document["id"], "bob")
    assert await visible(conn, document["id"], "dave")
    assert not await visible(conn, document["id"], "carol")
    assert await get_access(conn, document["id"], user_id="alice") == {
        "follows_folder": True,
        "folder": None,
        "folder_scope": None,
        "visibility": "private",
        "users": ["bob"],
        "groups": ["인사팀"],
    }


async def test_create_document_upload_with_grantees(conn):
    document = await create_document(
        conn,
        filename="규정.txt",
        data="업로드 본문".encode(),
        owner_id="alice",
        visibility="private",
        grant_users=["bob"],
        grant_groups=["인사팀"],
    )
    assert await visible(conn, document["id"], "bob")
    assert await visible(conn, document["id"], "dave")
    assert not await visible(conn, document["id"], "carol")


async def create_both(conn, **kwargs):
    await create_text_document(conn, title="t", content="본문", owner_id="alice", **kwargs)


async def upload_both(conn, **kwargs):
    await create_document(
        conn, filename="f.txt", data="본문".encode(), owner_id="alice", **kwargs
    )


@pytest.mark.parametrize("create", [create_both, upload_both])
@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"grant_users": ["bob"]}, GrantsOnPublicDocument),
        ({"visibility": "public", "grant_groups": ["인사팀"]}, GrantsOnPublicDocument),
        ({"visibility": "private", "grant_users": ["ghost"]}, UnknownGrantee),
        ({"visibility": "private", "grant_groups": ["유령팀"]}, UnknownGrantee),
        ({"visibility": "private", "grant_users": ["bob", "alice"]}, GrantToOwner),
    ],
)
async def test_create_with_invalid_grantees_creates_nothing(conn, create, kwargs, error):
    with pytest.raises(error):
        await create(conn, **kwargs)
    assert await document_count(conn) == 0
    cur = await conn.execute("SELECT count(*) FROM document_grants")
    assert await cur.fetchone() == (0,)


async def test_create_with_grantees_inside_caller_transaction_rolls_back_together(conn):
    """API는 호출부 트랜잭션 안에서 부른다. 모르는 이름이면 문서도 남지 않아야 한다."""
    with pytest.raises(UnknownGrantee):
        async with conn.transaction():
            await create_text_document(
                conn,
                title="t",
                content="본문",
                owner_id="alice",
                visibility="private",
                grant_users=["ghost"],
            )
    assert await document_count(conn) == 0


async def test_idempotency_key_ignores_order_and_duplicates_of_grantees(conn):
    first = await create_text_document(
        conn,
        title="t",
        content="본문",
        owner_id="alice",
        visibility="private",
        grant_users=["bob", "carol"],
        grant_groups=["인사팀"],
        idempotency_key="k1",
    )
    again = await create_text_document(
        conn,
        title="t",
        content="본문",
        owner_id="alice",
        visibility="private",
        grant_users=["carol", "bob", "bob"],
        grant_groups=["인사팀", "인사팀"],
        idempotency_key="k1",
    )
    assert again["id"] == first["id"]
    assert await document_count(conn) == 1
    with pytest.raises(IdempotencyKeyReused):
        await create_text_document(
            conn,
            title="t",
            content="본문",
            owner_id="alice",
            visibility="private",
            grant_users=["bob"],
            idempotency_key="k1",
        )


async def test_upload_idempotency_key_distinguishes_grantees(conn):
    kwargs = {
        "filename": "f.txt",
        "data": "본문".encode(),
        "owner_id": "alice",
        "visibility": "private",
        "idempotency_key": "k2",
    }
    first = await create_document(conn, grant_users=["bob", "carol"], **kwargs)
    again = await create_document(conn, grant_users=["carol", "bob"], **kwargs)
    assert again["id"] == first["id"]
    with pytest.raises(IdempotencyKeyReused):
        await create_document(conn, grant_groups=["인사팀"], **kwargs)


async def test_create_without_grantees_inserts_no_grants(conn):
    await create_text_document(
        conn, title="t", content="본문", owner_id="alice", visibility="private"
    )
    await create_document(conn, filename="f.txt", data="본문".encode(), owner_id="alice")
    cur = await conn.execute("SELECT count(*) FROM document_grants")
    assert await cur.fetchone() == (0,)


async def share_grant_count(conn, document_id):
    cur = await conn.execute(
        "SELECT count(*) FROM document_grants WHERE document_id = %s AND share_id IS NOT NULL",
        (document_id,),
    )
    return (await cur.fetchone())[0]


@pytest.mark.parametrize(
    ("start", "visibility", "users"),
    [("private", "public", []), ("public", "private", ["bob"])],
)
async def test_set_access_keeps_share_grants(conn, start, visibility, users):
    """공유 부여는 열람 범위와 별개 축이다 (ADR-044 「공유」 결정 2)."""
    doc = await insert_test_document(conn, title="d", content="본문", visibility=start)
    share = await create_share(conn, owner="alice", name="B사")
    await add_document(conn, share["id"], doc, owner="alice")
    audit_before = await access_audit_details(conn, doc)

    result = await set_access(
        conn, doc, user_id="alice", visibility=visibility, users=users, groups=[]
    )

    assert result == {
        "follows_folder": True,
        "folder": None,
        "folder_scope": None,
        "visibility": visibility,
        "users": users,
        "groups": [],
    }
    assert await share_grant_count(conn, doc) == 1
    assert await get_access(conn, doc, user_id="alice") == result
    assert await access_audit_details(conn, doc) == audit_before + [
        {"kind": "visibility", "before": start, "after": visibility},
    ] + [
        {"kind": "grant", "change": "added", "grantee_type": "user", "grantee": name}
        for name in users
    ]


async def test_set_public_without_grantees_succeeds_with_share_grant(conn):
    doc = await insert_test_document(conn, title="d", content="본문")
    share = await create_share(conn, owner="alice", name="B사")
    await add_document(conn, share["id"], doc, owner="alice")

    result = await set_access(conn, doc, user_id="alice", visibility="public", users=[], groups=[])

    assert result == {
        "follows_folder": True,
        "folder": None,
        "folder_scope": None,
        "visibility": "public",
        "users": [],
        "groups": [],
    }
    assert await share_grant_count(conn, doc) == 1
