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
    "document_trashed", "document_restored", "original_previewed", "owner_changed",
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
        assert audit_rows(conn) == before


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
        assert audit_rows(conn) == before
        assert conn.execute("SELECT count(*) FROM group_members").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM document_grants").fetchone()[0] == 0


def test_group_members_record_admin_and_ignore_conflicts(conn):
    with conn.transaction():
        set_actor(conn, "admin")
        user, group = principals(conn)
        conn.execute("INSERT INTO group_members VALUES (%s, %s)", (group, user))
        assert audit_rows(conn) == [
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


def test_folder_visibility_audit_and_unchanged_value(conn):
    folder = create_folder(conn)
    with conn.transaction():
        set_actor(conn, actor="kim")
        conn.execute("UPDATE folders SET visibility = 'private' WHERE id = %s", (folder,))
    expected = [("folder_access_changed", "kim", "session", None, None, {
        "kind": "visibility", "folder_id": str(folder), "folder_name": "사업 자료",
        "before": "public", "after": "private",
    })]
    assert audit_rows(conn) == expected
    conn.execute("UPDATE folders SET visibility = 'private' WHERE id = %s", (folder,))
    conn.execute("UPDATE folders SET name = '새 이름' WHERE id = %s", (folder,))
    assert audit_rows(conn) == expected


@pytest.mark.parametrize("grantee_type", ["user", "group"])
def test_folder_grant_audit(conn, grantee_type):
    folder = create_folder(conn)
    user, group = principals(conn)
    conn.execute("UPDATE groups SET name = '사업팀' WHERE id = %s", (group,))
    target = user if grantee_type == "user" else group
    column = "user_id" if grantee_type == "user" else "group_id"
    with conn.transaction():
        set_actor(conn, actor="kim")
        conn.execute(
            f"INSERT INTO folder_grants (folder_id, {column}) VALUES (%s, %s)",
            (folder, target),
        )
        conn.execute("DELETE FROM folder_grants WHERE folder_id = %s", (folder,))
    assert audit_rows(conn) == [
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
    assert audit_rows(conn) == before
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
    with conn.transaction():
        set_actor(conn, actor="kim", via="cli")
        conn.execute("UPDATE folders SET created_by = 'lee' WHERE id = %s", (folder,))
    expected = [("owner_changed", "kim", "cli", None, None, {
        "kind": "folder", "folder_id": str(folder), "folder_name": "사업 자료",
        "before": "alice", "after": "lee",
    })]
    assert audit_rows(conn) == expected
    conn.execute("UPDATE folders SET created_by = 'lee' WHERE id = %s", (folder,))
    assert audit_rows(conn) == expected


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
