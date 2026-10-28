# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""감사 행의 제약과 변경 거부를 실제 DB 소유자 연결로 검증한다 (ADR-055)."""

import re
from uuid import uuid4

import psycopg
import pytest

from openarchive.services.audit import ACTOR_VIA, AUDIT_ACTIONS


@pytest.fixture
def conn(migrated_db: str):
    with psycopg.connect(migrated_db, autocommit=True) as connection:
        yield connection


def test_audit_defaults(conn):
    before = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    row = conn.execute(
        "INSERT INTO audit_log (action) VALUES ('document_created') "
        "RETURNING id, occurred_at, db_role, detail, actor, actor_via"
    ).fetchone()
    after, role = conn.execute("SELECT clock_timestamp(), current_user").fetchone()
    assert row[0] > 0
    assert before <= row[1] <= after
    assert row[2:] == (role, {}, None, None)


@pytest.mark.parametrize("action", [
    "document_created", "text_updated", "document_deleted", "access_changed",
    "group_member_changed", "original_replaced", "original_downloaded", "folder_access_changed",
    "document_trashed", "document_restored", "original_previewed", "owner_changed", "share_changed", "group_changed", "user_changed",
    "password_changed", "folder_changed", "token_changed",
])
def test_allowed_actions(conn, action):
    assert conn.execute(
        "INSERT INTO audit_log (action) VALUES (%s) RETURNING action", (action,)
    ).fetchone() == (action,)


@pytest.mark.parametrize(("constraint", "expected"), [
    ("audit_log_action_valid", AUDIT_ACTIONS),
    ("audit_log_actor_via_valid", ACTOR_VIA),
])
def test_service_lists_match_db_constraints(conn, constraint, expected):
    # API 필터·set_actor 검증이 쓰는 목록과 DB CHECK가 어긋나면, 한쪽에만 있는 값은
    # 필터가 422로 막거나 기록이 CHECK에 걸린다 — 동작을 늘릴 때 둘을 함께 고치게 묶는다.
    definition = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s",
        (constraint,),
    ).fetchone()[0]
    assert set(re.findall(r"'([a-z_]+)'::text", definition)) == set(expected)


@pytest.mark.parametrize("via", [None, "session", "token", "mcp", "cli", "share", "worker"])
def test_allowed_actor_paths(conn, via):
    assert conn.execute(
        "INSERT INTO audit_log (action, actor_via) VALUES ('document_created', %s) "
        "RETURNING actor_via", (via,)
    ).fetchone() == (via,)


@pytest.mark.parametrize("column,value,constraint", [
    ("action", "unknown", "audit_log_action_valid"),
    ("action", "folder_created", "audit_log_action_valid"),
    ("actor_via", "unknown", "audit_log_actor_via_valid"),
])
def test_invalid_values_are_rejected(conn, column, value, constraint):
    with pytest.raises(psycopg.errors.CheckViolation) as error:
        conn.execute(
            f"INSERT INTO audit_log (action, {column}) VALUES ('document_created', %s)"
            if column != "action" else "INSERT INTO audit_log (action) VALUES (%s)",
            (value,),
        )
    assert error.value.diag.constraint_name == constraint


@pytest.mark.parametrize("statement", [
    "UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log",
])
def test_owner_cannot_change_audit_rows(conn, statement):
    role, owner = conn.execute(
        "SELECT current_user, pg_get_userbyid(relowner) FROM pg_class "
        "WHERE oid = 'audit_log'::regclass"
    ).fetchone()
    assert role == owner
    conn.execute("INSERT INTO audit_log (action, actor) VALUES ('document_created', 'alice')")
    original = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    with pytest.raises(psycopg.errors.RaiseException) as error:
        conn.execute(statement)
    assert error.value.diag.message_primary == "감사 로그는 고치거나 지울 수 없습니다."
    assert conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall() == original


def test_audit_survives_document_deletion_and_accepts_missing_document(conn):
    document_id = uuid4()
    conn.execute(
        "INSERT INTO audit_log (action, document_id, document_title) "
        "VALUES ('document_deleted', %s, '사건 시점 제목')", (document_id,),
    )
    original = conn.execute("SELECT * FROM audit_log").fetchall()
    conn.execute(
        "INSERT INTO documents (id, title, content_type, content, content_hash, owner_id) "
        "VALUES (%s, '현재 제목', 'md', '문서 텍스트', 'hash', 'alice')", (document_id,),
    )
    conn.execute("DELETE FROM documents WHERE id = %s", (document_id,))
    assert conn.execute("SELECT count(*) FROM documents WHERE id = %s", (document_id,)).fetchone()[0] == 0
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    assert rows[:len(original)] == original
    assert [row[2] for row in rows[len(original):]] == ["document_created", "document_deleted"]


def set_actor(conn, actor="alice", via="session", share_id=""):
    for key, value in (("actor_id", actor), ("actor_via", via), ("share_id", share_id)):
        conn.execute("SELECT set_config(%s, %s, true)", (f"openarchive.{key}", value))


def create_document(conn):
    return conn.execute(
        "INSERT INTO documents (title, content_type, content, content_hash, owner_id) "
        "VALUES ('감사 대상', 'md', '텍스트', 'hash1', 'alice') RETURNING id"
    ).fetchone()[0]


def audit_rows(conn):
    return conn.execute(
        "SELECT action, actor, actor_via, document_id, document_title, detail "
        "FROM audit_log ORDER BY id"
    ).fetchall()


def principals(conn):
    user = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES ('bob', 'hash') RETURNING id"
    ).fetchone()[0]
    group = conn.execute("INSERT INTO groups (name) VALUES ('개발팀') RETURNING id").fetchone()[0]
    return user, group


def add_file(conn, doc, version):
    conn.execute(
        "INSERT INTO document_files "
        "(document_id, file_version, filename, data, text_version, uploaded_by) "
        "VALUES (%s, %s, 'a.md', %s, 1, 'alice')", (doc, version, b"text"),
    )


def test_document_creation_and_text_update(conn):
    with conn.transaction():
        set_actor(conn)
        doc = create_document(conn)
        assert audit_rows(conn) == [("document_created", "alice", "session", doc, "감사 대상", {})]
        conn.execute(
            "UPDATE documents SET content='수정', content_hash='hash2', version=2 WHERE id=%s",
            (doc,),
        )
        assert audit_rows(conn)[1:] == [
            ("text_updated", "alice", "session", doc, "감사 대상", {"version": 2}),
        ]


def test_document_delete_preserves_title_and_history(conn):
    with conn.transaction():
        set_actor(conn)
        doc = create_document(conn)
        original = audit_rows(conn)
        conn.execute("DELETE FROM documents WHERE id=%s", (doc,))
        assert audit_rows(conn) == original + [
            ("document_deleted", "alice", "session", doc, "감사 대상", {}),
        ]


def test_document_delete_does_not_audit_cascades(conn):
    with conn.transaction():
        set_actor(conn)
        doc = create_document(conn)
        user, group = principals(conn)
        for column, target in (("user_id", user), ("group_id", group)):
            conn.execute(
                f"INSERT INTO document_grants (document_id, {column}) VALUES (%s, %s)",
                (doc, target),
            )
        add_file(conn, doc, 1)
        assert not any(row[0] == "original_replaced" for row in audit_rows(conn))
        add_file(conn, doc, 2)
        assert audit_rows(conn)[-1] == (
            "original_replaced", "alice", "session", doc, "감사 대상", {"file_version": 2},
        )
        before = audit_rows(conn)
        conn.execute("DELETE FROM documents WHERE id=%s", (doc,))
        assert audit_rows(conn)[len(before):] == [
            ("document_deleted", "alice", "session", doc, "감사 대상", {}),
        ]


def test_visibility_records_only_actual_changes(conn):
    with conn.transaction():
        set_actor(conn)
        doc = create_document(conn)
        conn.execute("UPDATE documents SET visibility='private' WHERE id=%s", (doc,))
        assert audit_rows(conn)[-1] == (
            "access_changed", "alice", "session", doc, "감사 대상",
            {"kind": "visibility", "before": "public", "after": "private"},
        )
        before = audit_rows(conn)
        conn.execute("UPDATE documents SET visibility='private' WHERE id=%s", (doc,))
        conn.execute("UPDATE documents SET title='새 제목', tags=ARRAY['태그'] WHERE id=%s", (doc,))
        assert audit_rows(conn) == before


def test_grants_record_names_and_exclude_shares(conn):
    with conn.transaction():
        set_actor(conn)
        doc = create_document(conn)
        user, group = principals(conn)
        for column, target, kind, name in (
            ("user_id", user, "user", "bob"), ("group_id", group, "group", "개발팀"),
        ):
            conn.execute(
                f"INSERT INTO document_grants (document_id, {column}) VALUES (%s, %s)",
                (doc, target),
            )
            assert audit_rows(conn)[-1] == (
                "access_changed", "alice", "session", doc, "감사 대상",
                {"kind": "grant", "change": "added", "grantee_type": kind, "grantee": name},
            )
            conn.execute(f"DELETE FROM document_grants WHERE {column}=%s", (target,))
            assert audit_rows(conn)[-1][-1] == {
                "kind": "grant", "change": "removed", "grantee_type": kind, "grantee": name,
            }
        share = conn.execute(
            "INSERT INTO shares (owner_user_id, name) VALUES (%s, '협업') RETURNING id", (user,),
        ).fetchone()[0]
        before = audit_rows(conn)
        conn.execute(
            "INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)", (doc, share),
        )
        conn.execute("DELETE FROM document_grants WHERE share_id=%s", (share,))
        assert audit_rows(conn)[len(before):] == [
            ("share_changed", "alice", "session", doc, "감사 대상",
             {"change": change, "share_id": str(share), "share_name": "협업", "owner": "bob"})
            for change in ("document_added", "document_removed")
        ]


@pytest.mark.parametrize("parent", ["groups", "users"])
def test_principal_delete_does_not_audit_cascades(conn, parent):
    with conn.transaction():
        set_actor(conn)
        doc = create_document(conn)
        user, group = principals(conn)
        conn.execute("INSERT INTO group_members VALUES (%s, %s)", (group, user))
        column, target = ("group_id", group) if parent == "groups" else ("user_id", user)
        conn.execute(
            f"INSERT INTO document_grants (document_id, {column}) VALUES (%s, %s)", (doc, target),
        )
        before = audit_rows(conn)
        conn.execute(f"DELETE FROM {parent} WHERE id=%s", (target,))
        action, key, name = ("group_changed", "group", "개발팀") if parent == "groups" else (
            "user_changed", "user", "bob",
        )
        assert audit_rows(conn)[len(before):] == [
            (action, "alice", "session", None, None, {"change": "deleted", key: name}),
        ]
        assert conn.execute("SELECT count(*) FROM group_members").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM document_grants").fetchone()[0] == 0


def test_group_members_record_admin_and_ignore_conflicts(conn):
    with conn.transaction():
        set_actor(conn, "admin")
        user, group = principals(conn)
        before = audit_rows(conn)
        conn.execute("INSERT INTO group_members VALUES (%s, %s)", (group, user))
        assert audit_rows(conn)[len(before):] == [
            ("group_member_changed", "admin", "session", None, None,
             {"change": "added", "group": "개발팀", "user": "bob"}),
        ]
        before = audit_rows(conn)
        conn.execute(
            "INSERT INTO group_members VALUES (%s, %s) ON CONFLICT DO NOTHING", (group, user),
        )
        assert audit_rows(conn) == before
        conn.execute("DELETE FROM group_members WHERE group_id=%s", (group,))
        assert audit_rows(conn)[-1] == (
            "group_member_changed", "admin", "session", None, None,
            {"change": "removed", "group": "개발팀", "user": "bob"},
        )


def test_original_preview_snapshot_and_missing_document(conn):
    with conn.transaction():
        set_actor(conn, actor="kim", via="token")
        doc = create_document(conn)
        add_file(conn, doc, 2)
        conn.execute("UPDATE documents SET title = '미리보기 시점 제목' WHERE id = %s", (doc,))
        before = audit_rows(conn)
        conn.execute("SELECT record_original_preview(%s, 2)", (doc,))
        assert audit_rows(conn) == before + [(
            "original_previewed", "kim", "token", doc, "미리보기 시점 제목",
            {"file_version": 2},
        )]
        before = audit_rows(conn)
        conn.execute("SELECT record_original_preview(%s, 2)", (uuid4(),))
        assert audit_rows(conn) == before


def test_original_download_snapshot_and_missing_document(conn):
    with conn.transaction():
        set_actor(conn)
        doc = create_document(conn)
        add_file(conn, doc, 1)
        conn.execute("SELECT record_original_download(%s, 1)", (doc,))
        assert audit_rows(conn)[-1] == (
            "original_downloaded", "alice", "session", doc, "감사 대상", {"file_version": 1},
        )
        before = audit_rows(conn)
        conn.execute("SELECT record_original_download(%s, 1)", (uuid4(),))
        assert audit_rows(conn) == before


@pytest.mark.parametrize("existing", [True, False])
def test_share_download_has_share_identity_without_owner(conn, existing):
    with conn.transaction():
        doc = create_document(conn)
        user, _ = principals(conn)
        share = conn.execute(
            "INSERT INTO shares (owner_user_id, name) VALUES (%s, '협업') RETURNING id", (user,),
        ).fetchone()[0] if existing else uuid4()
        set_actor(conn, "", "share", str(share))
        conn.execute("SELECT record_original_download(%s, 1)", (doc,))
        detail = {"file_version": 1, "share_id": str(share)}
        if existing:
            detail["share_name"] = "협업"
        assert audit_rows(conn)[-1] == (
            "original_downloaded", None, "share", doc, "감사 대상", detail,
        )


def test_actor_absent_on_fresh_and_reused_connection(conn):
    create_document(conn)
    with conn.transaction():
        set_actor(conn)
        create_document(conn)
    create_document(conn)
    assert conn.execute(
        "SELECT actor IS NULL, actor_via IS NULL, db_role = current_user "
        "FROM audit_log ORDER BY id"
    ).fetchall() == [(True, True, True), (False, False, True), (True, True, True)]


def test_rollback_removes_document_and_audit(conn):
    with pytest.raises(RuntimeError, match="rollback"), conn.transaction():
        set_actor(conn)
        create_document(conn)
        assert len(audit_rows(conn)) == 1
        raise RuntimeError("rollback")
    assert audit_rows(conn) == []
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 0


def create_folder(conn, name="사업 자료"):
    return conn.execute(
        "INSERT INTO folders (name, created_by, visibility) "
        "VALUES (%s, 'alice', 'public') RETURNING id", (name,),
    ).fetchone()[0]


def access_rows(conn):
    return [row for row in audit_rows(conn) if row[0] == "folder_access_changed"]


def test_folder_visibility_audit_and_unchanged_value(conn):
    folder = create_folder(conn)
    with conn.transaction():
        set_actor(conn, actor="kim")
        conn.execute("UPDATE folders SET visibility = 'private' WHERE id = %s", (folder,))
    expected = [("folder_access_changed", "kim", "session", None, None, {
        "kind": "visibility", "folder_id": str(folder), "folder_name": "사업 자료",
        "before": "public", "after": "private",
    })]
    assert access_rows(conn) == expected
    conn.execute("UPDATE folders SET visibility = 'private' WHERE id = %s", (folder,))
    conn.execute("UPDATE folders SET name = '새 이름' WHERE id = %s", (folder,))
    assert access_rows(conn) == expected


@pytest.mark.parametrize("grantee_type", ["user", "group"])
def test_folder_grant_audit(conn, grantee_type):
    folder = create_folder(conn)
    user, group = principals(conn)
    conn.execute("UPDATE groups SET name = '사업팀' WHERE id = %s", (group,))
    before = audit_rows(conn)
    target = user if grantee_type == "user" else group
    column = "user_id" if grantee_type == "user" else "group_id"
    with conn.transaction():
        set_actor(conn, actor="kim")
        conn.execute(
            f"INSERT INTO folder_grants (folder_id, {column}) VALUES (%s, %s)",
            (folder, target),
        )
        conn.execute("DELETE FROM folder_grants WHERE folder_id = %s", (folder,))
    assert audit_rows(conn)[len(before):] == [
        ("folder_access_changed", "kim", "session", None, None, {
            "kind": "grant", "change": change, "grantee_type": grantee_type,
            "grantee": "bob" if grantee_type == "user" else "사업팀",
            "folder_id": str(folder), "folder_name": "사업 자료",
        }) for change in ("added", "removed")
    ]


@pytest.mark.parametrize("parent", ["folders", "users", "groups"])
def test_folder_grant_cascade_is_not_access_change(conn, parent):
    folder = create_folder(conn)
    user, group = principals(conn)
    conn.execute("INSERT INTO folder_grants (folder_id, user_id) VALUES (%s, %s)", (folder, user))
    conn.execute("INSERT INTO folder_grants (folder_id, group_id) VALUES (%s, %s)", (folder, group))
    before = audit_rows(conn)
    target = {"folders": folder, "users": user, "groups": group}[parent]
    conn.execute(f"DELETE FROM {parent} WHERE id = %s", (target,))
    kind = "user" if parent == "users" else "group"
    expected = [
        ("folder_changed", None, None, None, None, {
            "change": "deleted", "folder_id": str(folder), "folder_name": "사업 자료",
            "parent_id": None,
        }),
    ] if parent == "folders" else [
        (kind + "_changed", None, None, None, None,
         {"change": "deleted", kind: "bob" if parent == "users" else "개발팀"}),
    ]
    assert audit_rows(conn)[len(before):] == expected
    assert conn.execute("SELECT count(*) FROM folder_grants").fetchone()[0] == (
        0 if parent == "folders" else 1
    )


def test_document_folder_inheritance_audit(conn):
    doc = create_document(conn)
    folder = create_folder(conn)
    conn.execute("UPDATE documents SET folder_id = %s WHERE id = %s", (folder, doc))
    before = len(audit_rows(conn))
    with conn.transaction():
        set_actor(conn, actor="kim")
        for follows in (False, False, True):
            conn.execute("UPDATE documents SET follows_folder = %s WHERE id = %s", (follows, doc))
    expected = [
        ("access_changed", "kim", "session", doc, "감사 대상", {
            "kind": "inherit", "before": old, "after": new,
        }) for old, new in (("folder", "own"), ("own", "folder"))
    ]
    assert audit_rows(conn)[before:] == expected
    conn.execute("UPDATE documents SET title = '새 제목' WHERE id = %s", (doc,))
    assert audit_rows(conn)[before:] == expected


@pytest.mark.parametrize("follows", [True, False])
def test_document_folder_move_audit(conn, follows):
    doc = create_document(conn)
    first = create_folder(conn)
    second = create_folder(conn, "개발 자료")
    conn.execute("UPDATE documents SET follows_folder = %s WHERE id = %s", (follows, doc))
    before = len(audit_rows(conn))
    with conn.transaction():
        set_actor(conn, actor="kim")
        for folder in (first, second, second, None):
            conn.execute("UPDATE documents SET folder_id = %s WHERE id = %s", (folder, doc))
    assert audit_rows(conn)[before:] == ([
        ("access_changed", "kim", "session", doc, "감사 대상", {
            "kind": "folder", "before": old, "after": new,
        }) for old, new in ((None, "사업 자료"), ("사업 자료", "개발 자료"), ("개발 자료", None))
    ] if follows else [])


@pytest.mark.parametrize("restored", [False, True])
def test_document_trash_transition_records_actor_and_title(conn, restored):
    doc = create_document(conn)
    if restored:
        conn.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (doc,))
    before = audit_rows(conn)
    with conn.transaction():
        set_actor(conn)
        conn.execute(
            "UPDATE documents SET deleted_at = " + ("NULL" if restored else "now()")
            + " WHERE id = %s", (doc,),
        )
    action = "document_restored" if restored else "document_trashed"
    assert audit_rows(conn) == before + [(action, "alice", "session", doc, "감사 대상", {})]


def test_document_trash_unchanged_nullness_is_not_audited(conn):
    doc = create_document(conn)
    before = audit_rows(conn)
    conn.execute("UPDATE documents SET deleted_at = NULL WHERE id = %s", (doc,))
    conn.execute("UPDATE documents SET title = '새 제목' WHERE id = %s", (doc,))
    assert audit_rows(conn) == before
    conn.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (doc,))
    before = audit_rows(conn)
    conn.execute(
        "UPDATE documents SET deleted_at = deleted_at + interval '1 day' WHERE id = %s", (doc,),
    )
    assert audit_rows(conn) == before


def test_trashed_document_delete_preserves_audit_history(conn):
    with conn.transaction():
        set_actor(conn)
        doc = create_document(conn)
        conn.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (doc,))
        original = audit_rows(conn)
        assert original[-1] == ("document_trashed", "alice", "session", doc, "감사 대상", {})
        conn.execute("DELETE FROM documents WHERE id = %s", (doc,))
    assert conn.execute("SELECT id FROM documents WHERE id = %s", (doc,)).fetchone() is None
    assert audit_rows(conn) == original + [
        ("document_deleted", "alice", "session", doc, "감사 대상", {}),
    ]


def test_worker_delete_records_worker_without_actor(conn):
    doc = create_document(conn)
    conn.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (doc,))
    with conn.transaction():
        set_actor(conn, actor="", via="worker")
        conn.execute("DELETE FROM documents WHERE id = %s", (doc,))
    assert audit_rows(conn)[-1] == ("document_deleted", None, "worker", doc, "감사 대상", {})


def test_document_owner_changed_audit(conn):
    doc = conn.execute(
        "INSERT INTO documents (title, content_type, content, content_hash, owner_id) "
        "VALUES ('감사 대상', 'md', '텍스트', 'hash1', 'kim') RETURNING id"
    ).fetchone()[0]
    with conn.transaction():
        set_actor(conn, actor="kim")
        conn.execute("UPDATE documents SET owner_id = 'lee' WHERE id = %s", (doc,))
    expected = [("owner_changed", "kim", "session", doc, "감사 대상", {
        "kind": "document", "before": "kim", "after": "lee",
    })]
    assert audit_rows(conn)[1:] == expected
    conn.execute("UPDATE documents SET owner_id = 'lee' WHERE id = %s", (doc,))
    conn.execute("UPDATE documents SET title = '새 제목' WHERE id = %s", (doc,))
    assert audit_rows(conn)[1:] == expected


@pytest.mark.parametrize("child", [False, True])
def test_folder_owner_changed_audit(conn, child):
    folder = create_folder(conn)
    if child:
        folder = conn.execute(
            "INSERT INTO folders (name, created_by, parent_id) "
            "VALUES ('사업 자료', 'alice', %s) RETURNING id", (folder,),
        ).fetchone()[0]
    before = len(audit_rows(conn))
    with conn.transaction():
        set_actor(conn, actor="kim", via="cli")
        conn.execute("UPDATE folders SET created_by = 'lee' WHERE id = %s", (folder,))
    expected = [("owner_changed", "kim", "cli", None, None, {
        "kind": "folder", "folder_id": str(folder), "folder_name": "사업 자료",
        "before": "alice", "after": "lee",
    })]
    assert audit_rows(conn)[before:] == expected
    conn.execute("UPDATE folders SET created_by = 'lee' WHERE id = %s", (folder,))
    assert audit_rows(conn)[before:] == expected


@pytest.mark.parametrize("table,column", [("documents", "owner_id"), ("folders", "created_by")])
def test_owner_changed_without_actor_and_rollback(conn, table, column):
    target = create_document(conn) if table == "documents" else create_folder(conn)
    before = audit_rows(conn)
    with pytest.raises(RuntimeError, match="rollback"), conn.transaction():
        conn.execute(f"UPDATE {table} SET {column} = 'lee' WHERE id = %s", (target,))
        row = audit_rows(conn)[-1]
        assert row[:3] == ("owner_changed", None, None)
        assert row[5]["before"] == "alice"
        assert row[5]["after"] == "lee"
        raise RuntimeError("rollback")
    assert audit_rows(conn) == before
    assert conn.execute(f"SELECT {column} FROM {table} WHERE id = %s", (target,)).fetchone() == (
        "alice",
    )


def create_share(conn, user):
    return conn.execute(
        "INSERT INTO shares (owner_user_id, name) VALUES (%s, '외부 협업') RETURNING id", (user,),
    ).fetchone()[0]


def create_share_token(conn, share):
    return conn.execute(
        "INSERT INTO api_tokens (share_id, name, token_hash, scope) "
        "VALUES (%s, '파트너', %s, 'read') RETURNING id", (share, str(uuid4())),
    ).fetchone()[0]


def share_event(share, change, actor="alice", doc=None, token=False):
    detail = {"change": change, "share_id": str(share), "share_name": "외부 협업", "owner": "bob"}
    if token:
        detail["token_name"] = "파트너"
    return ("share_changed", actor, "session", doc, "감사 대상" if doc else None, detail)


def test_share_creation_and_deletion_skip_cascades(conn):
    with conn.transaction():
        set_actor(conn)
        user, _ = principals(conn)
        doc = create_document(conn)
        before = audit_rows(conn)
        share = create_share(conn, user)
        assert audit_rows(conn)[len(before):] == [share_event(share, "created")]
        conn.execute("INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)",
                     (doc, share))
        create_share_token(conn, share)
        before = audit_rows(conn)
        conn.execute("DELETE FROM shares WHERE id=%s", (share,))
        assert audit_rows(conn)[len(before):] == [share_event(share, "deleted")]


def test_share_grants_ignore_conflicts_and_document_delete(conn):
    with conn.transaction():
        set_actor(conn)
        user, _ = principals(conn)
        share = create_share(conn, user)
        doc = create_document(conn)
        before = audit_rows(conn)
        sql = "INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)"
        conn.execute(sql, (doc, share))
        conn.execute(sql + " ON CONFLICT DO NOTHING", (doc, share))
        assert audit_rows(conn)[len(before):] == [share_event(share, "document_added", doc=doc)]
        before = audit_rows(conn)
        conn.execute("DELETE FROM documents WHERE id=%s", (doc,))
        assert audit_rows(conn)[len(before):] == [
            ("document_deleted", "alice", "session", doc, "감사 대상", {}),
        ]


def test_share_folder_added_removed_and_conflict_audit(conn):
    with conn.transaction():
        set_actor(conn, "admin")
        user, _ = principals(conn)
        share = create_share(conn, user)
        folder = create_folder(conn)
        before = audit_rows(conn)
        sql = "INSERT INTO share_folders (share_id, folder_id) VALUES (%s, %s)"
        conn.execute(sql, (share, folder))
        detail = {"change": "folder_added", "share_id": str(share),
                  "share_name": "외부 협업", "owner": "bob",
                  "folder_id": str(folder), "folder_name": "사업 자료"}
        assert audit_rows(conn)[len(before):] == [
            ("share_changed", "admin", "session", None, None, detail),
        ]
        before = audit_rows(conn)
        conn.execute(sql + " ON CONFLICT DO NOTHING", (share, folder))
        assert audit_rows(conn) == before
        conn.execute("DELETE FROM share_folders WHERE share_id=%s AND folder_id=%s", (share, folder))
        assert audit_rows(conn)[len(before):] == [
            ("share_changed", "admin", "session", None, None,
             {**detail, "change": "folder_removed"}),
        ]


@pytest.mark.parametrize("parent", ["share", "folder"])
def test_share_folder_cascade_skips_removal_audit(conn, parent):
    with conn.transaction():
        set_actor(conn)
        user, _ = principals(conn)
        share = create_share(conn, user)
        folder = create_folder(conn)
        conn.execute("INSERT INTO share_folders (share_id, folder_id) VALUES (%s, %s)", (share, folder))
        before = audit_rows(conn)
        table, target = ("shares", share) if parent == "share" else ("folders", folder)
        conn.execute(f"DELETE FROM {table} WHERE id=%s", (target,))
        assert audit_rows(conn)[len(before):] == (
            [share_event(share, "deleted")] if parent == "share" else [
                ("folder_changed", "alice", "session", None, None, {
                    "change": "deleted", "folder_id": str(folder),
                    "folder_name": "사업 자료", "parent_id": None,
                }),
            ]
        )
        assert conn.execute("SELECT count(*) FROM share_folders").fetchone()[0] == 0


def test_share_tokens_and_admin_revocation(conn):
    with conn.transaction():
        set_actor(conn)
        user, _ = principals(conn)
        share = create_share(conn, user)
        before = audit_rows(conn)
        token = create_share_token(conn, share)
        assert audit_rows(conn)[len(before):] == [share_event(share, "token_issued", token=True)]
        conn.execute("DELETE FROM api_tokens WHERE id=%s", (token,))
        assert audit_rows(conn)[-1] == share_event(share, "token_revoked", token=True)
        token = create_share_token(conn, share)
        set_actor(conn, "admin")
        conn.execute("DELETE FROM api_tokens WHERE id=%s", (token,))
        assert audit_rows(conn)[-1] == share_event(share, "token_revoked", "admin", token=True)
        before = audit_rows(conn)
        token = conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, scope) "
            "VALUES (%s, '개인', %s, 'read') RETURNING id", (user, str(uuid4())),
        ).fetchone()[0]
        conn.execute("DELETE FROM api_tokens WHERE id=%s", (token,))
        # 사용자 토큰은 공유 변경이 아니라 token_changed로만 남는다 (ADR-055 2026-10-12 개정).
        assert [row[0] for row in audit_rows(conn)[len(before):]] == ["token_changed"] * 2


@pytest.mark.parametrize("table,key,name", [("users", "username", "새 사용자"),
                                          ("groups", "name", "새 그룹")])
def test_principal_creation_and_deletion(conn, table, key, name):
    with conn.transaction():
        set_actor(conn, "admin")
        before = audit_rows(conn)
        extra = ", password_hash" if table == "users" else ""
        values = ", 'hash'" if table == "users" else ""
        target = conn.execute(
            f"INSERT INTO {table} ({key}{extra}) VALUES (%s{values}) RETURNING id", (name,),
        ).fetchone()[0]
        conn.execute(f"DELETE FROM {table} WHERE id=%s", (target,))
        kind = "user" if table == "users" else "group"
        created = {"change": "created", kind: name}
        if table == "users":
            created["is_admin"] = False
        assert audit_rows(conn)[len(before):] == [
            (kind + "_changed", "admin", "session", None, None, created),
            (kind + "_changed", "admin", "session", None, None, {"change": "deleted", kind: name}),
        ]


def test_user_delete_skips_all_share_and_principal_cascades(conn):
    with conn.transaction():
        set_actor(conn, "admin")
        user, group = principals(conn)
        share = create_share(conn, user)
        doc = create_document(conn)
        create_share_token(conn, share)
        conn.execute("INSERT INTO group_members VALUES (%s, %s)", (group, user))
        conn.execute("INSERT INTO document_grants (document_id, user_id) VALUES (%s, %s)",
                     (doc, user))
        conn.execute("INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)",
                     (doc, share))
        folder = create_folder(conn)
        conn.execute("INSERT INTO share_folders (share_id, folder_id) VALUES (%s, %s)", (share, folder))
        create_user_token(conn, user)
        before = audit_rows(conn)
        conn.execute("DELETE FROM users WHERE id=%s", (user,))
        assert audit_rows(conn)[len(before):] == [
            ("user_changed", "admin", "session", None, None, {"change": "deleted", "user": "bob"}),
        ]


def test_group_creation_without_actor(conn):
    before = audit_rows(conn)
    conn.execute("INSERT INTO groups (name) VALUES ('직접 생성')")
    assert audit_rows(conn)[len(before):] == [
        ("group_changed", None, None, None, None, {"change": "created", "group": "직접 생성"}),
    ]


# ── 2026-10-12 개정: 비밀번호·관리자 권한·폴더·사용자 토큰 (ADR-055) ──


def create_user_token(conn, user, expires_at=None):
    token_hash = "secret-hash-" + str(uuid4())
    token = conn.execute(
        "INSERT INTO api_tokens (user_id, name, token_hash, scope, expires_at) "
        "VALUES (%s, '개인', %s, 'read_write', %s) RETURNING id",
        (user, token_hash, expires_at),
    ).fetchone()[0]
    return token, token_hash


def test_password_change_records_only_actual_changes(conn):
    with conn.transaction():
        set_actor(conn)
        user, _ = principals(conn)
        before = audit_rows(conn)
        conn.execute("UPDATE users SET password_hash = 'new-secret-hash' WHERE id=%s", (user,))
        conn.execute("UPDATE users SET password_hash = 'new-secret-hash' WHERE id=%s", (user,))
        conn.execute("UPDATE users SET username = 'bobby' WHERE id=%s", (user,))
        assert audit_rows(conn)[len(before):] == [
            ("password_changed", "alice", "session", None, None, {"user": "bob"}),
        ]


def test_password_change_without_actor_keeps_db_role(conn):
    user, _ = principals(conn)
    conn.execute("UPDATE users SET password_hash = 'reset' WHERE id=%s", (user,))
    assert conn.execute(
        "SELECT action, actor, actor_via, db_role = current_user, detail "
        "FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone() == ("password_changed", None, None, True, {"user": "bob"})


def test_admin_flag_changes_record_grant_and_revoke(conn):
    user, _ = principals(conn)
    before = audit_rows(conn)
    for value in (True, True, False):
        conn.execute("UPDATE users SET is_admin = %s WHERE id=%s", (value, user))
    assert audit_rows(conn)[len(before):] == [
        ("user_changed", None, None, None, None, {"change": change, "user": "bob"})
        for change in ("admin_granted", "admin_revoked")
    ]


def test_admin_user_creation_records_admin_flag(conn):
    with conn.transaction():
        set_actor(conn, "admin", via="cli")
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES ('boss', 'hash', true)"
        )
    assert audit_rows(conn)[-1] == ("user_changed", "admin", "cli", None, None, {
        "change": "created", "user": "boss", "is_admin": True,
    })


def test_folder_created_renamed_and_deleted(conn):
    with conn.transaction():
        set_actor(conn, "admin")
        root = create_folder(conn)
        child = conn.execute(
            "INSERT INTO folders (name, created_by, parent_id) "
            "VALUES ('제안서', 'alice', %s) RETURNING id", (root,),
        ).fetchone()[0]
        conn.execute("UPDATE folders SET name = '제안서 초안' WHERE id = %s", (child,))
        conn.execute("UPDATE folders SET name = '제안서 초안' WHERE id = %s", (child,))
        conn.execute("UPDATE folders SET updated_at = now() WHERE id = %s", (child,))
        conn.execute("DELETE FROM folders WHERE id = %s", (child,))
    assert audit_rows(conn) == [
        ("folder_changed", "admin", "session", None, None, detail) for detail in (
            {"change": "created", "folder_id": str(root), "folder_name": "사업 자료",
             "parent_id": None},
            {"change": "created", "folder_id": str(child), "folder_name": "제안서",
             "parent_id": str(root)},
            {"change": "renamed", "folder_id": str(child), "folder_name": "제안서 초안",
             "old_name": "제안서", "parent_id": str(root)},
            {"change": "deleted", "folder_id": str(child), "folder_name": "제안서 초안",
             "parent_id": str(root)},
        )
    ]


def test_folder_scope_and_owner_changes_are_not_folder_changes(conn):
    folder = create_folder(conn)
    conn.execute("UPDATE folders SET visibility = 'private' WHERE id = %s", (folder,))
    conn.execute("UPDATE folders SET created_by = 'lee' WHERE id = %s", (folder,))
    assert [row[0] for row in audit_rows(conn)] == [
        "folder_changed", "folder_access_changed", "owner_changed",
    ]


def test_folder_change_rolls_back_with_the_folder(conn):
    with pytest.raises(RuntimeError, match="rollback"), conn.transaction():
        create_folder(conn)
        assert len(audit_rows(conn)) == 1
        raise RuntimeError("rollback")
    assert audit_rows(conn) == []


def test_user_token_issue_and_revoke_without_secret(conn):
    with conn.transaction():
        set_actor(conn, "bob")
        user, _ = principals(conn)
        before = audit_rows(conn)
        token, token_hash = create_user_token(conn, user, "2027-01-01T00:00:00+00:00")
        expires = conn.execute(
            "SELECT to_jsonb(expires_at) FROM api_tokens WHERE id=%s", (token,)
        ).fetchone()[0]
        no_expiry, _ = create_user_token(conn, user)
        set_actor(conn, "admin")
        conn.execute("DELETE FROM api_tokens WHERE id=%s", (token,))
        rows = audit_rows(conn)[len(before):]
    base = {"owner": "bob", "token_name": "개인", "scope": "read_write"}
    assert expires is not None
    assert rows == [
        ("token_changed", "bob", "session", None, None,
         {**base, "change": "issued", "expires_at": expires}),
        ("token_changed", "bob", "session", None, None,
         {**base, "change": "issued", "expires_at": None}),
        ("token_changed", "admin", "session", None, None,
         {**base, "change": "revoked", "expires_at": expires}),
    ]
    assert token_hash not in str(rows)
    assert str(token) not in str(rows) and str(no_expiry) not in str(rows)


def test_user_delete_skips_user_token_revocation(conn):
    with conn.transaction():
        set_actor(conn, "admin")
        user, _ = principals(conn)
        create_user_token(conn, user)
        before = audit_rows(conn)
        conn.execute("DELETE FROM users WHERE id=%s", (user,))
        assert audit_rows(conn)[len(before):] == [
            ("user_changed", "admin", "session", None, None, {"change": "deleted", "user": "bob"}),
        ]


def test_department_changes_audit_actor_noop_and_membership_removal(conn):
    user = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES ('kim', 'hash') RETURNING id"
    ).fetchone()[0]
    groups = [conn.execute(
        "INSERT INTO groups (name) VALUES (%s) RETURNING id", (name,),
    ).fetchone()[0] for name in ("IT", "HR")]
    for group in groups:
        conn.execute("INSERT INTO group_members VALUES (%s, %s)", (group, user))
    with conn.transaction():
        set_actor(conn)
        for group in (groups[0], groups[0], groups[1]):
            conn.execute("UPDATE users SET department_group_id = %s WHERE id = %s", (group, user))
        conn.execute("DELETE FROM group_members WHERE group_id = %s", (groups[1],))
    rows = [r for r in audit_rows(conn) if r[0] == "user_changed" and r[5].get("change") == "department_changed"]
    assert rows == [
        ("user_changed", "alice", "session", None, None,
         {"change": "department_changed", "user": "kim", "from": before, "to": after})
        for before, after in ((None, "IT"), ("IT", "HR"), ("HR", None))
    ]


def test_department_folder_audit_and_group_cascade(conn):
    group = conn.execute("INSERT INTO groups (name) VALUES ('IT') RETURNING id").fetchone()[0]
    folder = create_folder(conn)
    with conn.transaction():
        set_actor(conn)
        for value in (group, group, None, group):
            conn.execute("UPDATE folders SET department_group_id = %s WHERE id = %s", (value, folder))
        conn.execute("DELETE FROM groups WHERE id = %s", (group,))
    rows = [r for r in audit_rows(conn) if r[0] == "folder_changed" and r[5].get("change", "").startswith("department_")]
    assert rows == [
        ("folder_changed", "alice", "session", None, None,
         {"change": change, "folder_id": str(folder), "folder_name": "사업 자료", "department": department})
        for change, department in (
            ("department_set", "IT"), ("department_cleared", "IT"),
            ("department_set", "IT"), ("department_cleared", None),
        )
    ]


def test_department_group_deletion_audit_and_rollback(conn):
    user = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES ('kim', 'hash') RETURNING id"
    ).fetchone()[0]
    group = conn.execute("INSERT INTO groups (name) VALUES ('IT') RETURNING id").fetchone()[0]
    conn.execute("INSERT INTO group_members VALUES (%s, %s)", (group, user))
    conn.execute("UPDATE users SET department_group_id = %s WHERE id = %s", (group, user))
    before = audit_rows(conn)
    with pytest.raises(RuntimeError, match="rollback"), conn.transaction():
        set_actor(conn)
        conn.execute("DELETE FROM groups WHERE id = %s", (group,))
        assert conn.execute(
            "SELECT department_group_id FROM users WHERE id = %s", (user,),
        ).fetchone() == (None,)
        raise RuntimeError("rollback")
    assert audit_rows(conn) == before
    assert conn.execute(
        "SELECT department_group_id FROM users WHERE id = %s", (user,),
    ).fetchone() == (group,)
    with conn.transaction():
        set_actor(conn)
        conn.execute("DELETE FROM groups WHERE id = %s", (group,))
    rows = [r for r in audit_rows(conn)[len(before):] if r[0] == "user_changed"]
    assert rows == [("user_changed", "alice", "session", None, None, {
        "change": "department_changed", "user": "kim", "from": None, "to": None,
    })]


@pytest.mark.parametrize("removal", ["manager", "membership"])
def test_department_manager_changes_record_actor(conn, removal):
    user, group = principals(conn)
    conn.execute("INSERT INTO group_members VALUES (%s, %s)", (group, user))
    before = audit_rows(conn)
    with conn.transaction():
        set_actor(conn, "department-lead")
        conn.execute(
            "INSERT INTO group_managers (group_id, user_id) VALUES (%s, %s)", (group, user),
        )
        table = "group_managers" if removal == "manager" else "group_members"
        conn.execute(f"DELETE FROM {table} WHERE group_id = %s", (group,))
    changes = [row for row in audit_rows(conn)[len(before):] if row[0] == "group_changed"]
    assert changes == [
        ("group_changed", "department-lead", "session", None, None,
         {"change": change, "group": "개발팀", "user": "bob"})
        for change in ("manager_added", "manager_removed")
    ]
    assert conn.execute("SELECT * FROM group_managers").fetchall() == []


@pytest.mark.parametrize("parent", ["groups", "users"])
def test_department_manager_principal_cascade_skips_manager_audit(conn, parent):
    user, group = principals(conn)
    conn.execute("INSERT INTO group_members VALUES (%s, %s)", (group, user))
    conn.execute(
        "INSERT INTO group_managers (group_id, user_id) VALUES (%s, %s)", (group, user),
    )
    before = audit_rows(conn)
    conn.execute(f"DELETE FROM {parent} WHERE id = %s", (group if parent == "groups" else user,))
    assert conn.execute(f"SELECT id FROM {parent}").fetchall() == []
    assert conn.execute("SELECT * FROM group_managers").fetchall() == []
    assert audit_rows(conn)[len(before):] == [
        ("group_changed" if parent == "groups" else "user_changed",
         None, None, None, None,
         {"change": "deleted", "group": "개발팀"} if parent == "groups"
         else {"change": "deleted", "user": "bob"})
    ]


def test_department_description_audits_only_actual_changes(conn):
    _, group = principals(conn)
    before = audit_rows(conn)
    with conn.transaction():
        set_actor(conn, "department-lead")
        conn.execute("UPDATE groups SET description = '개발 부서' WHERE id = %s", (group,))
        conn.execute("UPDATE groups SET description = '개발 부서' WHERE id = %s", (group,))
    assert audit_rows(conn)[len(before):] == [
        ("group_changed", "department-lead", "session", None, None,
         {"change": "description_changed", "group": "개발팀"})
    ]
