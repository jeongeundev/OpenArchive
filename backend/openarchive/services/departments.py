# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""부서 디렉터리와 구성원 관리 위임."""

from uuid import UUID

from psycopg.rows import dict_row

from openarchive.services import grants
from openarchive.services.visibility import FOLDER_VISIBLE_TO_USER, VISIBLE_TO_USER


async def list_departments(conn, *, user_id: str, is_admin: bool) -> list[dict]:
    """모든 부서와 구성원, 열람 범위 안의 폴더·개별 부여 문서 수."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            SELECT g.id, g.name, g.description, f.id AS folder_id,
                   COALESCE((
                       SELECT jsonb_agg(jsonb_build_object(
                           'username', u.username, 'manager', gm.user_id IS NOT NULL,
                           'primary', COALESCE(u.department_group_id = g.id, false))
                           ORDER BY (gm.user_id IS NOT NULL) DESC, u.username)
                       FROM group_members m JOIN users u ON u.id = m.user_id
                       LEFT JOIN group_managers gm
                         ON gm.group_id = m.group_id AND gm.user_id = m.user_id
                       WHERE m.group_id = g.id
                   ), '[]'::jsonb) AS members,
                   (SELECT count(*) FROM documents d
                    WHERE {VISIBLE_TO_USER}
                      AND EXISTS (SELECT 1 FROM document_grants dg
                                  WHERE dg.document_id = d.id AND dg.group_id = g.id)
                   ) AS granted_document_count,
                   (%(admin)s OR EXISTS (
                       SELECT 1 FROM group_managers gm JOIN users u ON u.id = gm.user_id
                       WHERE gm.group_id = g.id AND u.username = %(user)s
                   )) AS can_manage
            FROM groups g
            LEFT JOIN folders f ON f.department_group_id = g.id AND {FOLDER_VISIBLE_TO_USER}
            ORDER BY g.name
            """,
            {"user": user_id, "admin": is_admin},
        )
        return await cur.fetchall()


class NotDepartmentManager(Exception):
    """부서 관리자도 조직 관리자도 아니다."""


class ManagerMustBeMember(ValueError):
    """부서 관리자는 그 부서의 구성원이어야 한다."""


async def ensure_can_manage(
    conn, group_id: UUID, *, user_id: str, is_admin: bool
) -> None:
    cur = await conn.execute("SELECT id FROM groups WHERE id = %s", (group_id,))
    if await cur.fetchone() is None:
        raise grants.GroupNotFound("그룹을 찾을 수 없습니다.")
    if is_admin:
        return
    cur = await conn.execute(
        """SELECT 1 FROM group_managers gm JOIN users u ON u.id = gm.user_id
           WHERE gm.group_id = %s AND u.username = %s""",
        (group_id, user_id),
    )
    if await cur.fetchone() is None:
        raise NotDepartmentManager("부서 관리자 권한이 필요합니다.")


async def add_department_member(
    conn, group_id, username, *, user_id, is_admin
) -> None:
    async with conn.transaction():
        await ensure_can_manage(conn, group_id, user_id=user_id, is_admin=is_admin)
        await grants.add_member(conn, group_id, username)


async def remove_department_member(
    conn, group_id, username, *, user_id, is_admin
) -> None:
    async with conn.transaction():
        await ensure_can_manage(conn, group_id, user_id=user_id, is_admin=is_admin)
        await grants.remove_member(conn, group_id, username)


async def set_department_manager(
    conn, group_id, username, manager: bool, *, user_id, is_admin
) -> None:
    async with conn.transaction():
        await ensure_can_manage(conn, group_id, user_id=user_id, is_admin=is_admin)
        if manager:
            # FK 대상 구성원을 잠가 지정 도중 제거되는 경쟁도 막는다.
            cur = await conn.execute(
                """SELECT m.user_id FROM group_members m JOIN users u ON u.id = m.user_id
                   WHERE m.group_id = %s AND u.username = %s FOR KEY SHARE OF m""",
                (group_id, username),
            )
            member = await cur.fetchone()
            if member is None:
                raise ManagerMustBeMember("부서 관리자는 구성원이어야 합니다.")
            await conn.execute(
                """INSERT INTO group_managers (group_id, user_id) VALUES (%s, %s)
                   ON CONFLICT DO NOTHING""",
                (group_id, member[0]),
            )
        else:
            await conn.execute(
                """DELETE FROM group_managers gm USING users u
                   WHERE gm.user_id = u.id AND gm.group_id = %s AND u.username = %s""",
                (group_id, username),
            )


async def update_department(
    conn, group_id, *, description: str, user_id, is_admin
) -> dict:
    async with conn.transaction():
        await ensure_can_manage(conn, group_id, user_id=user_id, is_admin=is_admin)
        description = description.strip()
        if len(description) > 200:
            raise ValueError("부서 설명은 200자 이하여야 합니다.")
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """UPDATE groups SET description = %s WHERE id = %s
                   RETURNING id, name, description""",
                (description, group_id),
            )
            return await cur.fetchone()
