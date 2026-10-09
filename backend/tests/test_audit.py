"""행위자 전달과 REST 감사 기록을 실제 pgvector DB에서 검증한다."""

import asyncio
from uuid import UUID, uuid4

import psycopg
import pytest
from conftest import insert_test_document, login_as, upload_document
from psycopg_pool import AsyncConnectionPool
from test_groups_api import create_group, ensure_user, login_admin
from test_share_access import issue_share_token
from test_token_access import bearer, issue_token

from openarchive import db
from openarchive.services.audit import AUDIT_ACTIONS, set_actor
from openarchive.services.documents import DocumentNotFound, get_original_file


def rows(dsn, document_id=None):
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT action, actor, actor_via, document_title, detail FROM audit_log "
            "WHERE (%s::uuid IS NULL OR document_id = %s) ORDER BY id",
            (document_id, document_id),
        ).fetchall()


async def test_actor_is_transaction_local(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        await set_actor(conn, actor="alice", via="session")
        doc = await insert_test_document(conn, title="감사", content="처음")
        await conn.commit()
        other = await insert_test_document(conn, title="다음", content="다음")
        await conn.commit()
    assert rows(migrated_db, doc) == [("document_created", "alice", "session", "감사", {})]
    assert rows(migrated_db, other) == [("document_created", None, None, "다음", {})]


async def test_actor_does_not_leak_through_app_pool(migrated_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    pool = AsyncConnectionPool(migrated_db, min_size=1, max_size=1, open=False)
    monkeypatch.setattr(db, "_pool", pool)
    await pool.open()
    try:
        async with db.connection() as conn:
            pid = conn.info.backend_pid
            await set_actor(conn, actor="alice", via="session")
            await insert_test_document(conn, title="첫 대여", content="첫")
        async with db.connection() as conn:
            assert conn.info.backend_pid == pid
            doc = await insert_test_document(conn, title="둘째 대여", content="둘째")
        assert rows(migrated_db, doc)[0][1:3] == (None, None)
    finally:
        await db.close_pool()


@pytest.mark.parametrize("via,share_id", [("unknown", None), ("share", None), ("session", uuid4())])
async def test_invalid_actor_path(migrated_db, via, share_id):
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        with pytest.raises(ValueError):
            await set_actor(conn, actor="alice", via=via, share_id=share_id)


async def test_autocommit_requires_transaction(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        with pytest.raises(RuntimeError, match="트랜잭션 안에서 불러야 한다"):
            await set_actor(conn, actor="alice", via="session")
        async with conn.transaction():
            await set_actor(conn, actor="alice", via="session")
            doc = await insert_test_document(conn, title="명시적", content="글")
    assert rows(migrated_db, doc)[0][1:3] == ("alice", "session")


async def test_repeated_actor_clears_previous_values(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        await set_actor(conn, actor=None, via="share", share_id=uuid4())
        await set_actor(conn, actor="alice", via="session")
        doc = await insert_test_document(conn, title="재설정", content="글")
        await conn.commit()
    assert rows(migrated_db, doc)[0] == ("document_created", "alice", "session", "재설정", {})


def test_session_creation_edit_access_and_delete(db_client, migrated_db):
    login_as(db_client, "alice")
    response = db_client.post("/api/documents/text", json={"title": "사건 제목", "content": "처음"})
    assert response.status_code == 201
    doc = response.json()["id"]
    assert rows(migrated_db, doc) == [("document_created", "alice", "session", "사건 제목", {})]
    assert (
        db_client.put(f"/api/documents/{doc}", json={"content": "수정", "version": 1}).status_code
        == 200
    )
    assert rows(migrated_db, doc)[-1] == (
        "text_updated",
        "alice",
        "session",
        "사건 제목",
        {"version": 2},
    )
    assert (
        db_client.put(
            f"/api/documents/{doc}/access",
            json={"visibility": "private", "users": [], "groups": []},
        ).status_code
        == 200
    )
    assert rows(migrated_db, doc)[-1] == (
        "access_changed",
        "alice",
        "session",
        "사건 제목",
        {"kind": "visibility", "before": "public", "after": "private"},
    )
    before = rows(migrated_db, doc)
    assert db_client.delete(f"/api/documents/{doc}").status_code == 204
    assert rows(migrated_db, doc) == before + [
        ("document_trashed", "alice", "session", "사건 제목", {})
    ]
    assert db_client.delete(f"/api/documents/{doc}?permanent=true").status_code == 204
    assert rows(migrated_db, doc) == before + [
        ("document_trashed", "alice", "session", "사건 제목", {}),
        ("document_deleted", "alice", "session", "사건 제목", {}),
    ]


def test_token_creation_records_owner_and_token(db_client, migrated_db):
    token = issue_token(db_client, "alice", scope="read_write")["token"]
    db_client.cookies.clear()
    response = db_client.post(
        "/api/documents/text", headers=bearer(token), json={"title": "토큰", "content": "글"}
    )
    assert response.status_code == 201
    assert rows(migrated_db, response.json()["id"]) == [
        ("document_created", "alice", "token", "토큰", {})
    ]


def test_group_changes_record_admin(db_client, migrated_db):
    login_admin(db_client, migrated_db)
    ensure_user(migrated_db, "bob")
    group = create_group(db_client)
    before = rows(migrated_db)
    path = f"/api/admin/groups/{group['id']}/members/bob"
    assert db_client.put(path).status_code == 204
    assert db_client.delete(path).status_code == 204
    assert rows(migrated_db)[len(before):] == [
        (
            "group_member_changed",
            "boss",
            "session",
            None,
            {"change": change, "group": group["name"], "user": "bob"},
        )
        for change in ("added", "removed")
    ]


def test_original_replacement_and_download_versions(db_client, migrated_db):
    doc = upload_document(db_client).json()["id"]
    response = db_client.put(
        f"/api/documents/{doc}/file",
        files={"file": ("new.txt", b"new text")},
        data={"current_version": "1"},
    )
    assert response.status_code == 200
    assert rows(migrated_db, doc)[-1][0:3] == ("original_replaced", "alice", "session")
    assert rows(migrated_db, doc)[-1][-1] == {"file_version": 2}
    for path, version, data in (("file", 2, b"new text"), ("files/1", 1, b"OpenSQL guide")):
        response = db_client.get(f"/api/documents/{doc}/{path}")
        assert response.status_code == 200
        assert response.content == data
        assert rows(migrated_db, doc)[-1] == (
            "original_downloaded",
            "alice",
            "session",
            "guide",
            {"file_version": version},
        )


def test_share_original_download(db_client, migrated_db):
    doc = upload_document(db_client).json()["id"]
    response = db_client.post("/api/shares", json={"name": "협업 공유"})
    assert response.status_code == 201
    share = response.json()["id"]
    assert db_client.put(f"/api/shares/{share}/documents/{doc}").status_code == 204
    token = issue_share_token(db_client, share)["token"]
    db_client.cookies.clear()
    assert db_client.get(f"/api/documents/{doc}/file", headers=bearer(token)).status_code == 200
    assert rows(migrated_db, doc)[-1] == (
        "original_downloaded",
        None,
        "share",
        "guide",
        {"file_version": 1, "share_id": share, "share_name": "협업 공유"},
    )


def test_invisible_original_does_not_record_download(db_client, migrated_db):
    doc = upload_document(db_client, data={"visibility": "private"}).json()["id"]
    login_as(db_client, "bob")
    before = rows(migrated_db, doc)
    assert db_client.get(f"/api/documents/{doc}/file").status_code == 404
    assert rows(migrated_db, doc) == before

    async def denied_service_call():
        # 거절을 호출자가 처리해 커밋해도 감사 부작용이 없어야 한다.
        async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
            await set_actor(conn, actor="bob", via="session")
            with pytest.raises(DocumentNotFound):
                await get_original_file(conn, UUID(doc), user_id="bob", file_version=1)

    asyncio.run(denied_service_call())
    assert rows(migrated_db, doc) == before


def test_trash_actions_are_available_for_audit_filters():
    assert {"document_trashed", "document_restored"} <= set(AUDIT_ACTIONS)


async def test_list_audit_unlimited_preserves_filters_and_order(migrated_db):
    from openarchive.services.audit import list_audit

    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        await set_actor(conn, actor="exporter", via="session")
        for i in range(55):
            await insert_test_document(conn, title=str(i), content="글")
        await set_actor(conn, actor="other", via="session")
        await insert_test_document(conn, title="제외", content="글")
        result = await list_audit(conn, actor="exporter", action="document_created", limit=None)
        assert len(result) == 55
        assert [r["id"] for r in result] == sorted((r["id"] for r in result), reverse=True)
        assert all(r["actor"] == "exporter" and r["action"] == "document_created" for r in result)
        assert len(await list_audit(conn, actor="exporter")) == 50
        assert await list_audit(conn, actor="other", action="share_changed", limit=None) == []


@pytest.mark.parametrize("title", ["=SUM(1)", "+1", "-1", "@x", "\t제목", "\r제목", '일반, "제목"\n다음'])
def test_render_audit_csv_roundtrip_and_formula_safety(title):
    import csv
    import io
    import json
    from datetime import UTC, datetime

    from openarchive.services.audit import render_audit_csv

    occurred = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    detail = {"share_name": '한글, "공유"\n다음'}
    row = {
        "id": 1, "occurred_at": occurred, "actor": "+관리자", "actor_via": "session",
        "db_role": "app", "action": "share_changed", "document_id": None,
        "document_title": title, "detail": detail,
    }
    output = render_audit_csv([row])
    assert output.startswith("\ufeffid,occurred_at,actor,actor_via,db_role,action,document_id,document_title,detail\r\n")
    [parsed] = list(csv.DictReader(io.StringIO(output.removeprefix("\ufeff"))))
    assert parsed["occurred_at"] == occurred.isoformat()
    assert parsed["document_id"] == ""
    assert parsed["actor"] == "'+관리자"
    assert parsed["document_title"] == ("'" + title if title[0] in "=+-@\t\r" else title)
    assert json.loads(parsed["detail"]) == detail
    assert "한글" in parsed["detail"]
    row["actor"] = row["actor_via"] = row["document_title"] = None
    [nulls] = list(csv.DictReader(io.StringIO(render_audit_csv([row])[1:])))
    assert nulls["actor"] == nulls["actor_via"] == nulls["document_title"] == ""
