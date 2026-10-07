"""폴더 열람·관리. 범위 요약의 부여 대상 이름은 폴더를 보는 사용자에게 공개한다."""

from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from openarchive.services.documents import InvalidVisibility, _check_grantees
from openarchive.services.grants import resolve_grantees
from openarchive.services.visibility import (
    FOLDER_VISIBLE_TO_USER,
    VISIBILITY_VALUES,
    VISIBLE_TO_USER,
)


class FolderNotFound(Exception):
    """볼 수 있는 폴더가 없다."""


class FolderNotEmpty(Exception):
    def __init__(self):
        super().__init__("폴더가 비어 있지 않습니다.")


class FolderNameTaken(Exception):
    def __init__(self):
        super().__init__("같은 이름의 폴더가 이미 있습니다.")


class SubfolderScope(Exception):
    def __init__(self):
        super().__init__("하위 폴더는 상위 폴더의 열람 범위를 따릅니다.")


class NotFolderCreator(Exception):
    def __init__(self):
        super().__init__("폴더를 관리할 권한이 없습니다.")


_SCOPE = """r.visibility,
    COALESCE((SELECT array_agg(u.username ORDER BY u.username)
              FROM folder_grants g JOIN users u ON u.id=g.user_id
              WHERE g.folder_id=r.id), ARRAY[]::text[]) AS users,
    COALESCE((SELECT array_agg(gr.name ORDER BY gr.name)
              FROM folder_grants g JOIN groups gr ON gr.id=g.group_id
              WHERE g.folder_id=r.id), ARRAY[]::text[]) AS groups"""


def _name(name):
    name = name.strip()
    if not name or "/" in name:
        raise ValueError("폴더 이름은 공백이 아니어야 하며 /를 포함할 수 없습니다.")
    return name


async def ensure_folder_visible(conn, folder_id: UUID, *, user_id: str | None) -> dict:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"SELECT f.* FROM folders f WHERE f.id=%(id)s AND {FOLDER_VISIBLE_TO_USER}",
        {"id": folder_id, "user": user_id},
    )
    row = await cur.fetchone()
    if row is None:
        raise FolderNotFound("폴더를 찾을 수 없습니다.")
    return row


async def folder_path(conn, folder_id: UUID) -> list[dict]:
    """열람 확인을 마친 폴더의 내부 경로 조회. 호출자가 먼저 열람을 확인한다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """WITH RECURSIVE path AS (
        SELECT id, name, parent_id, 0 AS depth FROM folders WHERE id=%s
        UNION ALL SELECT p.id, p.name, p.parent_id, c.depth+1
        FROM folders p JOIN path c ON p.id=c.parent_id)
        SELECT id, name FROM path ORDER BY depth DESC""",
        (folder_id,),
    )
    return await cur.fetchall()


async def _insert_grants(conn, folder_id, user_ids, group_ids):
    await conn.cursor().executemany(
        "INSERT INTO folder_grants (folder_id,user_id,group_id) VALUES (%s,%s,%s)",
        [(folder_id, u, None) for u in user_ids] + [(folder_id, None, g) for g in group_ids],
    )


async def create_folder(
    conn,
    *,
    user_id: str,
    name: str,
    parent_id: UUID | None = None,
    visibility: str | None = None,
    grant_users: list[str] | None = None,
    grant_groups: list[str] | None = None,
) -> dict:
    async with conn.transaction():
        if parent_id is not None:
            await ensure_folder_visible(conn, parent_id, user_id=user_id)
            if visibility is not None or grant_users is not None or grant_groups is not None:
                raise SubfolderScope
            scope = None
            user_ids, group_ids = [], []
        else:
            if visibility is None:
                visibility = "public"
            if visibility not in VISIBILITY_VALUES:
                raise InvalidVisibility("공개범위는 public, private 중 하나여야 합니다.")
            users, groups = _check_grantees(visibility, user_id, grant_users, grant_groups)
            user_ids, group_ids = await resolve_grantees(conn, users=users, groups=groups)
            scope = visibility
        cur = conn.cursor(row_factory=dict_row)
        try:
            await cur.execute(
                """INSERT INTO folders (parent_id,name,created_by,visibility)
                VALUES (%s,%s,%s,%s) RETURNING *""",
                (parent_id, _name(name), user_id, scope),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise FolderNameTaken from exc
        row = await cur.fetchone()
        await _insert_grants(conn, row["id"], user_ids, group_ids)
        return row


async def find_folder(
    conn,
    *,
    user_id: str | None,
    name: str,
    parent_id: UUID | None = None,
    created_by: str | None = None,
) -> dict | None:
    """import 재실행이 다시 쓸, `user_id`가 볼 수 있는 폴더의 id와 범위.

    최상위 이름은 유일하지 않으므로(030) `created_by`로 좁히고, 여럿이면 가장 오래된 것을 준다.
    """
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""SELECT r.id, {_SCOPE} FROM folders r
        WHERE r.name=%(name)s AND r.parent_id IS NOT DISTINCT FROM %(parent)s
          AND (%(by)s::text IS NULL OR r.created_by=%(by)s)
          AND EXISTS (SELECT 1 FROM folders f WHERE f.id=r.id AND {FOLDER_VISIBLE_TO_USER})
        ORDER BY r.created_at, r.id LIMIT 1""",
        {"name": name, "parent": parent_id, "by": created_by, "user": user_id},
    )
    return await cur.fetchone()


async def list_folders(conn, *, user_id: str | None, is_admin: bool = False) -> list[dict]:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""SELECT f.id, f.parent_id, f.name, f.created_by,
        (SELECT count(*) FROM documents d WHERE d.folder_id=f.id AND {VISIBLE_TO_USER}) AS document_count,
        jsonb_build_object('visibility', scope.visibility, 'users', scope.users, 'groups', scope.groups) AS scope,
        (f.parent_id IS NOT NULL) AS inherited,
        COALESCE(f.created_by=%(user)s OR %(admin)s, false) AS can_manage,
        COALESCE(f.parent_id IS NULL AND f.created_by=%(user)s, false) AS can_change_access
        FROM folders f
        CROSS JOIN LATERAL (
            WITH RECURSIVE ancestors AS (
                SELECT id,parent_id,visibility FROM folders WHERE id=f.id
                UNION ALL SELECT p.id,p.parent_id,p.visibility FROM folders p
                JOIN ancestors a ON p.id=a.parent_id)
            SELECT {_SCOPE} FROM ancestors r WHERE r.parent_id IS NULL
        ) scope
        WHERE {FOLDER_VISIBLE_TO_USER} ORDER BY f.created_at, f.id""",
        {"user": user_id, "admin": is_admin},
    )
    return await cur.fetchall()


async def rename_folder(conn, folder_id: UUID, *, user_id: str, is_admin: bool, name: str) -> dict:
    async with conn.transaction():
        row = await ensure_folder_visible(conn, folder_id, user_id=user_id)
        if row["created_by"] != user_id and not is_admin:
            raise NotFolderCreator
        cur = conn.cursor(row_factory=dict_row)
        try:
            await cur.execute(
                "UPDATE folders SET name=%s, updated_at=now() WHERE id=%s RETURNING *",
                (_name(name), folder_id),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise FolderNameTaken from exc
        row = await cur.fetchone()
        if row is None:
            raise FolderNotFound
        return row


async def delete_folder(conn, folder_id: UUID, *, user_id: str, is_admin: bool) -> None:
    async with conn.transaction():
        row = await ensure_folder_visible(conn, folder_id, user_id=user_id)
        if row["created_by"] != user_id and not is_admin:
            raise NotFolderCreator
        cur = await conn.execute(
            """SELECT EXISTS(SELECT 1 FROM folders WHERE parent_id=%s)
            OR EXISTS(SELECT 1 FROM documents WHERE folder_id=%s)""",
            (folder_id, folder_id),
        )
        if (await cur.fetchone())[0]:
            raise FolderNotEmpty
        try:
            cur = await conn.execute("DELETE FROM folders WHERE id=%s", (folder_id,))
        except psycopg.errors.ForeignKeyViolation as exc:
            raise FolderNotEmpty from exc
        if cur.rowcount == 0:
            raise FolderNotFound


async def _creator(conn, folder_id, user_id):
    row = await ensure_folder_visible(conn, folder_id, user_id=user_id)
    if row["created_by"] != user_id:
        raise NotFolderCreator
    if row["parent_id"] is not None:
        raise SubfolderScope
    return row


async def _read_access(conn, folder_id):
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(f"SELECT {_SCOPE} FROM folders r WHERE r.id=%s", (folder_id,))
    row = await cur.fetchone()
    if row is None:
        raise FolderNotFound
    return row


async def get_folder_access(conn, folder_id: UUID, *, user_id: str) -> dict:
    await _creator(conn, folder_id, user_id)
    return await _read_access(conn, folder_id)


async def set_folder_access(
    conn, folder_id: UUID, *, user_id: str, visibility: str, users: list[str], groups: list[str]
) -> dict:
    async with conn.transaction():
        await _creator(conn, folder_id, user_id)
        cur = await conn.execute(
            "SELECT 1 FROM folders WHERE id=%s FOR NO KEY UPDATE", (folder_id,)
        )
        if await cur.fetchone() is None:
            raise FolderNotFound
        if visibility not in VISIBILITY_VALUES:
            raise InvalidVisibility("공개범위는 public, private 중 하나여야 합니다.")
        users, groups = _check_grantees(visibility, user_id, users, groups)
        user_ids, group_ids = await resolve_grantees(conn, users=users, groups=groups)
        current = await (
            await conn.execute(
                "SELECT user_id,group_id FROM folder_grants WHERE folder_id=%s", (folder_id,)
            )
        ).fetchall()
        old_users = {r[0] for r in current if r[0] is not None}
        old_groups = {r[1] for r in current if r[1] is not None}
        await conn.execute(
            "UPDATE folders SET visibility=%s, updated_at=now() WHERE id=%s",
            (visibility, folder_id),
        )
        await conn.execute(
            """DELETE FROM folder_grants WHERE folder_id=%s
            AND (user_id=ANY(%s) OR group_id=ANY(%s))""",
            (folder_id, list(old_users - set(user_ids)), list(old_groups - set(group_ids))),
        )
        await _insert_grants(
            conn,
            folder_id,
            [u for u in user_ids if u not in old_users],
            [g for g in group_ids if g not in old_groups],
        )
        return await _read_access(conn, folder_id)
