"""감사 행의 제약과 변경 거부를 실제 DB 소유자 연결로 검증한다 (ADR-055)."""

from uuid import uuid4

import psycopg
import pytest


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
    "group_member_changed", "original_replaced", "original_downloaded",
])
def test_allowed_actions(conn, action):
    assert conn.execute(
        "INSERT INTO audit_log (action) VALUES (%s) RETURNING action", (action,)
    ).fetchone() == (action,)


@pytest.mark.parametrize("via", [None, "session", "token", "mcp", "cli", "share", "worker"])
def test_allowed_actor_paths(conn, via):
    assert conn.execute(
        "INSERT INTO audit_log (action, actor_via) VALUES ('document_created', %s) "
        "RETURNING actor_via", (via,)
    ).fetchone() == (via,)


@pytest.mark.parametrize("column,value,constraint", [
    ("action", "unknown", "audit_log_action_valid"),
    ("action", "folder_access_changed", "audit_log_action_valid"),
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
    assert conn.execute("SELECT * FROM audit_log").fetchall() == original
