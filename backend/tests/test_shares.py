"""공유 관리 서비스 — 공유·공유 부여·공유 토큰 (ADR-044 「공유」)."""

from uuid import uuid4

import psycopg
import pytest
from conftest import insert_test_document

from openarchive.services.auth import TokenNotFound, create_token, hash_token, list_tokens
from openarchive.services.documents import DocumentAccessDenied, DocumentNotFound
from openarchive.services.shares import (
    ShareAlreadyExists,
    ShareNotFound,
    add_document,
    create_share,
    delete_share,
    issue_share_token,
    list_shares,
    remove_document,
    revoke_share_token,
)


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as connection:
        for username, is_admin in [("alice", False), ("bob", False), ("root", True)]:
            await connection.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, 'unused', %s)",
                (username, is_admin),
            )
        yield connection


async def share_grants(conn, share_id):
    cur = await conn.execute(
        "SELECT document_id FROM document_grants WHERE share_id = %s ORDER BY 1", (share_id,)
    )
    return [row[0] for row in await cur.fetchall()]


async def share_token_rows(conn, share_id):
    cur = await conn.execute(
        "SELECT id, user_id, scope, token_hash FROM api_tokens WHERE share_id = %s", (share_id,)
    )
    return await cur.fetchall()


async def test_create_share_trims_name_and_starts_empty(conn):
    share = await create_share(conn, owner="alice", name="  B사 협업  ")
    assert share["name"] == "B사 협업"
    assert share["documents"] == []
    assert share["tokens"] == []
    assert set(share) == {"id", "name", "created_at", "documents", "tokens"}


async def test_create_share_rejects_blank_name(conn):
    with pytest.raises(ValueError):
        await create_share(conn, owner="alice", name="   ")


async def test_share_names_are_unique_per_owner(conn):
    await create_share(conn, owner="alice", name="B사")
    with pytest.raises(ShareAlreadyExists):
        await create_share(conn, owner="alice", name="B사")
    other = await create_share(conn, owner="bob", name="B사")
    assert other["name"] == "B사"


async def test_list_shares_returns_only_own_shares_with_documents_and_tokens(conn):
    share = await create_share(conn, owner="alice", name="B사")
    await create_share(conn, owner="bob", name="bob의 공유")
    zeta = await insert_test_document(conn, title="제타", content="본문")
    alpha = await insert_test_document(conn, title="알파", content="본문", visibility="private")
    await add_document(conn, share["id"], zeta, owner="alice")
    await add_document(conn, share["id"], alpha, owner="alice")
    issued = await issue_share_token(conn, share["id"], owner="alice", name="연동")

    shares = await list_shares(conn, owner="alice")

    assert [s["name"] for s in shares] == ["B사"]
    assert shares[0]["documents"] == [
        {"id": alpha, "title": "알파"},
        {"id": zeta, "title": "제타"},
    ]
    [token] = shares[0]["tokens"]
    assert set(token) == {"id", "name", "scope", "created_at", "expires_at", "last_used_at", "expired"}
    assert token["id"] == issued["id"]
    assert token["scope"] == "read"


async def test_delete_share_removes_grants_and_tokens(conn):
    share = await create_share(conn, owner="alice", name="B사")
    doc = await insert_test_document(conn, title="d", content="본문")
    await add_document(conn, share["id"], doc, owner="alice")
    await issue_share_token(conn, share["id"], owner="alice", name="t")

    await delete_share(conn, share["id"], owner="alice")

    assert await share_grants(conn, share["id"]) == []
    assert await share_token_rows(conn, share["id"]) == []
    assert await list_shares(conn, owner="alice") == []


@pytest.mark.parametrize("which", ["others", "missing"])
async def test_delete_share_hides_others_and_missing_alike(conn, which):
    share = await create_share(conn, owner="bob", name="bob의 공유")
    share_id = share["id"] if which == "others" else uuid4()
    with pytest.raises(ShareNotFound):
        await delete_share(conn, share_id, owner="alice")
    assert [s["name"] for s in await list_shares(conn, owner="bob")] == ["bob의 공유"]


@pytest.mark.parametrize("visibility", ["public", "private"])
async def test_add_own_document_is_idempotent(conn, visibility):
    share = await create_share(conn, owner="alice", name="B사")
    doc = await insert_test_document(conn, title="d", content="본문", visibility=visibility)
    await add_document(conn, share["id"], doc, owner="alice")
    await add_document(conn, share["id"], doc, owner="alice")
    assert await share_grants(conn, share["id"]) == [doc]


@pytest.mark.parametrize("operation", [add_document, remove_document])
async def test_document_operations_require_own_share(conn, operation):
    others = await create_share(conn, owner="bob", name="bob의 공유")
    doc = await insert_test_document(conn, title="d", content="본문")
    with pytest.raises(ShareNotFound):
        await operation(conn, others["id"], doc, owner="alice")
    with pytest.raises(ShareNotFound):
        await operation(conn, uuid4(), doc, owner="alice")
    assert await share_grants(conn, others["id"]) == []


@pytest.mark.parametrize("operation", [add_document, remove_document])
async def test_document_operations_require_own_document(conn, operation):
    share = await create_share(conn, owner="alice", name="B사")
    hidden = await insert_test_document(
        conn, title="h", content="본문", owner_id="bob", visibility="private"
    )
    others_public = await insert_test_document(conn, title="p", content="본문", owner_id="bob")
    with pytest.raises(DocumentNotFound):
        await operation(conn, share["id"], hidden, owner="alice")
    with pytest.raises(DocumentNotFound):
        await operation(conn, share["id"], uuid4(), owner="alice")
    # 보이는 문서라도 남의 것은 공유에 넣지 못한다 — 조직 공개 문서를 외부로 내보내는 경로가 된다
    with pytest.raises(DocumentAccessDenied):
        await operation(conn, share["id"], others_public, owner="alice")
    assert await share_grants(conn, share["id"]) == []


async def test_admin_cannot_share_others_documents(conn):
    share = await create_share(conn, owner="root", name="관리자 공유")
    doc = await insert_test_document(conn, title="d", content="본문")
    with pytest.raises(DocumentAccessDenied):
        await add_document(conn, share["id"], doc, owner="root")
    assert await share_grants(conn, share["id"]) == []


async def test_remove_document_is_idempotent(conn):
    share = await create_share(conn, owner="alice", name="B사")
    doc = await insert_test_document(conn, title="d", content="본문")
    await add_document(conn, share["id"], doc, owner="alice")
    await remove_document(conn, share["id"], doc, owner="alice")
    await remove_document(conn, share["id"], doc, owner="alice")
    assert await share_grants(conn, share["id"]) == []


async def test_issue_share_token_stores_only_hash(conn):
    share = await create_share(conn, owner="alice", name="B사")
    issued = await issue_share_token(conn, share["id"], owner="alice", name="연동")

    assert issued["scope"] == "read"
    assert issued["name"] == "연동"
    assert issued["token"]
    [(token_id, user_id, scope, token_hash)] = await share_token_rows(conn, share["id"])
    assert token_id == issued["id"]
    assert user_id is None
    assert scope == "read"
    assert token_hash == hash_token(issued["token"])
    assert token_hash != issued["token"]


async def test_issue_share_token_requires_own_share(conn):
    others = await create_share(conn, owner="bob", name="bob의 공유")
    with pytest.raises(ShareNotFound):
        await issue_share_token(conn, others["id"], owner="alice", name="t")
    assert await share_token_rows(conn, others["id"]) == []


async def test_revoke_share_token(conn):
    share = await create_share(conn, owner="alice", name="B사")
    issued = await issue_share_token(conn, share["id"], owner="alice", name="t")
    await revoke_share_token(conn, share["id"], issued["id"], owner="alice")
    assert await share_token_rows(conn, share["id"]) == []
    with pytest.raises(TokenNotFound):
        await revoke_share_token(conn, share["id"], issued["id"], owner="alice")


async def test_revoke_share_token_deletes_nothing_outside_the_share(conn):
    mine = await create_share(conn, owner="alice", name="B사")
    other_mine = await create_share(conn, owner="alice", name="C사")
    bobs = await create_share(conn, owner="bob", name="bob의 공유")
    token_in_other = await issue_share_token(conn, other_mine["id"], owner="alice", name="t")
    bobs_token = await issue_share_token(conn, bobs["id"], owner="bob", name="t")
    alice_id = (
        await (await conn.execute("SELECT id FROM users WHERE username = 'alice'")).fetchone()
    )[0]
    user_token = await create_token(conn, alice_id, name="사용자 토큰")

    # 다른 공유의 토큰, 사용자 토큰은 이 공유의 토큰이 아니다
    for token_id in (token_in_other["id"], user_token["id"]):
        with pytest.raises(TokenNotFound):
            await revoke_share_token(conn, mine["id"], token_id, owner="alice")
    # 남의 공유로는 그 공유의 토큰도 지우지 못한다
    with pytest.raises(ShareNotFound):
        await revoke_share_token(conn, bobs["id"], bobs_token["id"], owner="alice")

    assert len(await share_token_rows(conn, other_mine["id"])) == 1
    assert len(await share_token_rows(conn, bobs["id"])) == 1
    assert [t["id"] for t in await list_tokens(conn, alice_id)] == [user_token["id"]]


async def test_user_token_list_excludes_share_tokens(conn):
    share = await create_share(conn, owner="alice", name="B사")
    await issue_share_token(conn, share["id"], owner="alice", name="공유 토큰")
    alice_id = (
        await (await conn.execute("SELECT id FROM users WHERE username = 'alice'")).fetchone()
    )[0]
    assert await list_tokens(conn, alice_id) == []


@pytest.mark.parametrize("offset", ["0 seconds", "-1 second"])
async def test_share_token_expiry_rejects_nonfuture(conn, offset):
    from openarchive.services.auth import InvalidTokenExpiry

    share = await create_share(conn, owner="alice", name="expiry")
    async with conn.transaction():
        expiry = (await (await conn.execute("SELECT now() + %s::interval", (offset,))).fetchone())[0]
        with pytest.raises(InvalidTokenExpiry, match="만료일은 지금 이후여야 합니다."):
            await issue_share_token(conn, share["id"], owner="alice", name="invalid", expires_at=expiry)
        assert await share_token_rows(conn, share["id"]) == []


@pytest.mark.parametrize("expiry", ["omitted", "none", "future"])
async def test_share_token_expiry_issue_validate_and_list(conn, expiry):
    from openarchive.services.auth import AuthenticationFailed, validate_token

    share = await create_share(conn, owner="alice", name="expiry")
    future = (await (await conn.execute("SELECT now() + interval '1 hour'")).fetchone())[0]
    kwargs = {} if expiry == "omitted" else {"expires_at": future if expiry == "future" else None}
    issued = await issue_share_token(conn, share["id"], owner="alice", name="token", **kwargs)
    assert issued["expires_at"] == (future if expiry == "future" else None)
    assert issued["last_used_at"] is None and issued["expired"] is False
    assert (await validate_token(conn, issued["token"]))["share_id"] == share["id"]
    listed = (await list_shares(conn, owner="alice"))[0]["tokens"][0]
    assert listed["expires_at"] == issued["expires_at"]
    assert listed["last_used_at"] is not None and listed["expired"] is False
    assert "token" not in listed and "token_hash" not in listed
    await conn.execute("UPDATE api_tokens SET expires_at = now() - interval '1 second' WHERE id = %s", (issued["id"],))
    with pytest.raises(AuthenticationFailed):
        await validate_token(conn, issued["token"])
    expired = (await list_shares(conn, owner="alice"))[0]["tokens"][0]
    assert expired["expired"] is True
    assert expired["last_used_at"] == listed["last_used_at"]


async def test_list_all_shares_metadata_and_trash(conn):
    import json

    from openarchive.services.shares import list_all_shares

    bob = await create_share(conn, owner="bob", name="A")
    zeta = await create_share(conn, owner="alice", name="Z")
    alpha = await create_share(conn, owner="alice", name="A")
    doc = await insert_test_document(conn, title="비밀 제목", content="본문")
    trash = await insert_test_document(conn, title="휴지통 비밀", content="본문")
    for document in (doc, trash):
        await add_document(conn, alpha["id"], document, owner="alice")
    await conn.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (trash,))
    token = await issue_share_token(conn, alpha["id"], owner="alice", name="만료 토큰")
    await conn.execute(
        "UPDATE api_tokens SET expires_at = now() - interval '1 second' WHERE id = %s",
        (token["id"],),
    )
    result = await list_all_shares(conn)
    assert [s["id"] for s in result] == [alpha["id"], zeta["id"], bob["id"]]
    assert all(set(s) == {"id", "name", "owner", "created_at", "document_count", "tokens"} for s in result)
    assert [s["owner"] for s in result] == ["alice", "alice", "bob"]
    assert [s["document_count"] for s in result] == [1, 0, 0]
    assert result[1]["tokens"] == result[2]["tokens"] == []
    [listed] = result[0]["tokens"]
    assert set(listed) == {"id", "name", "created_at", "expires_at", "last_used_at", "expired"}
    assert listed["id"] == token["id"] and listed["expired"] is True
    serialized = json.dumps(result, default=str)
    for hidden in ("비밀 제목", "휴지통 비밀", str(doc), str(trash), token["token"]):
        assert hidden not in serialized


async def test_admin_revoke_share_token_boundaries(conn):
    from openarchive.services.audit import list_audit, set_actor
    from openarchive.services.auth import AuthenticationFailed, validate_token
    from openarchive.services.shares import admin_revoke_share_token

    share = await create_share(conn, owner="bob", name="남의 공유")
    other = await create_share(conn, owner="alice", name="다른 공유")
    token = await issue_share_token(conn, share["id"], owner="bob", name="폐기")
    other_token = await issue_share_token(conn, other["id"], owner="alice", name="유지")
    user_id = (await (await conn.execute("SELECT id FROM users WHERE username = 'alice'")).fetchone())[0]
    user_token = await create_token(conn, user_id, name="사용자")
    for wrong in (other_token["id"], user_token["id"], uuid4()):
        with pytest.raises(TokenNotFound):
            await admin_revoke_share_token(conn, share["id"], wrong)
    assert await validate_token(conn, other_token["token"])
    assert await validate_token(conn, user_token["token"])
    assert await validate_token(conn, token["token"])
    async with conn.transaction():
        await set_actor(conn, actor="root", via="session")
        await admin_revoke_share_token(conn, share["id"], token["id"])
    assert await share_token_rows(conn, share["id"]) == []
    with pytest.raises(AuthenticationFailed):
        await validate_token(conn, token["token"])
    [event] = await list_audit(conn, actor="root", action="share_changed")
    assert event["detail"]["owner"] == "bob"
    assert event["detail"]["change"] == "token_revoked"
