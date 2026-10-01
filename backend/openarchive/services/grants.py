"""그룹·구성원 관리와 부여 대상 해석.

부여는 사람이 내린 결정의 기록이라 앱이 INSERT한다(025 주석).
그룹 부여는 관리자를 신뢰한다(ADR-044 관리 경로 결정 1).
"""

from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from openarchive.services.auth import UserNotFound


class GroupAlreadyExists(Exception):
    """같은 이름의 그룹이 이미 존재한다."""


class GroupNotFound(Exception):
    """대상 그룹이 존재하지 않는다."""


class UnknownGrantee(Exception):
    """지정한 부여 대상 이름이 존재하지 않는다."""

    def __init__(self, kind: Literal["user", "group"], names: list[str]):
        self.kind = kind
        self.names = names
        label = "사용자" if kind == "user" else "그룹"
        super().__init__(f"알 수 없는 {label}: {', '.join(names)}")


async def create_group(conn: psycopg.AsyncConnection, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("그룹 이름을 입력하세요.")
    cur = conn.cursor(row_factory=dict_row)
    try:
        await cur.execute(
            "INSERT INTO groups (name) VALUES (%s) RETURNING id, name, created_at",
            (name,),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise GroupAlreadyExists("이미 존재하는 그룹 이름입니다.") from exc
    group = await cur.fetchone()
    group["members"] = []
    return group


async def list_groups(conn: psycopg.AsyncConnection) -> list[dict]:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT g.id, g.name, g.created_at,
               COALESCE(array_agg(u.username ORDER BY u.username)
                        FILTER (WHERE u.id IS NOT NULL), ARRAY[]::text[]) AS members
        FROM groups g
        LEFT JOIN group_members m ON m.group_id = g.id
        LEFT JOIN users u ON u.id = m.user_id
        GROUP BY g.id
        ORDER BY g.name
        """
    )
    return await cur.fetchall()


async def delete_group(conn: psycopg.AsyncConnection, group_id: UUID) -> None:
    cur = await conn.execute("DELETE FROM groups WHERE id = %s", (group_id,))
    if cur.rowcount == 0:
        raise GroupNotFound("그룹을 찾을 수 없습니다.")


async def _member_user_id(conn: psycopg.AsyncConnection, group_id: UUID, username: str) -> UUID:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute("SELECT id FROM groups WHERE id = %s", (group_id,))
    if await cur.fetchone() is None:
        raise GroupNotFound("그룹을 찾을 수 없습니다.")
    await cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    user = await cur.fetchone()
    if user is None:
        raise UserNotFound("사용자를 찾을 수 없습니다.")
    return user["id"]


async def add_member(conn: psycopg.AsyncConnection, group_id: UUID, username: str) -> None:
    user_id = await _member_user_id(conn, group_id, username)
    await conn.execute(
        "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (group_id, user_id),
    )


async def remove_member(conn: psycopg.AsyncConnection, group_id: UUID, username: str) -> None:
    user_id = await _member_user_id(conn, group_id, username)
    await conn.execute(
        "DELETE FROM group_members WHERE group_id = %s AND user_id = %s",
        (group_id, user_id),
    )


async def list_principals(conn: psycopg.AsyncConnection) -> dict:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute("SELECT username FROM users ORDER BY username")
    users = [row["username"] for row in await cur.fetchall()]
    await cur.execute("SELECT name FROM groups ORDER BY name")
    groups = [row["name"] for row in await cur.fetchall()]
    return {"users": users, "groups": groups}


async def resolve_grantees(
    conn: psycopg.AsyncConnection, *, users: list[str], groups: list[str]
) -> tuple[list[UUID], list[UUID]]:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute("SELECT id, username FROM users WHERE username = ANY(%s)", (users,))
    user_ids = {row["username"]: row["id"] for row in await cur.fetchall()}
    unknown_users = [name for name in dict.fromkeys(users) if name not in user_ids]
    if unknown_users:
        raise UnknownGrantee("user", unknown_users)
    await cur.execute("SELECT id, name FROM groups WHERE name = ANY(%s)", (groups,))
    group_ids = {row["name"]: row["id"] for row in await cur.fetchall()}
    unknown_groups = [name for name in dict.fromkeys(groups) if name not in group_ids]
    if unknown_groups:
        raise UnknownGrantee("group", unknown_groups)
    return (
        [user_ids[name] for name in dict.fromkeys(users)],
        [group_ids[name] for name in dict.fromkeys(groups)],
    )


async def insert_grants(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    user_ids: list[UUID],
    group_ids: list[UUID],
) -> None:
    cur = conn.cursor()
    await cur.executemany(
        """
        INSERT INTO document_grants (document_id, user_id, group_id)
        VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
        """,
        [(document_id, user_id, None) for user_id in user_ids]
        + [(document_id, None, group_id) for group_id in group_ids],
    )
