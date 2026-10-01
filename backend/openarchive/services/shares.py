"""공유 관리 — 외부 협업용 공유 주체와 그 부여·읽기 전용 토큰 (ADR-044 「공유」).

공유는 소유자가 지정한 문서 집합을 읽는 주체다. 공유 부여도 사람이 내린 결정의
기록이라 앱이 INSERT하며(026), 그 경로는 이 모듈 하나다 — 공유에 문서를 넣을 수
있는 사람이 소유자뿐이라는 판정이 한 곳에 있어야 한다.

- 남의 공유와 없는 공유는 구별하지 않는다(`ShareNotFound`). 공유의 존재가 새지 않는다.
- 문서는 자기 것만 넣는다. 보이는 남의 문서(조직 공개 포함)를 넣을 수 있으면 조직 안
  열람 범위가 조직 밖으로 번진다. 관리자도 예외가 아니다.
- 공유 토큰은 `read` 고정이고 DB에는 sha256만 남는다(ADR-034).
"""

from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from openarchive.services.auth import SCOPE_READ, TokenNotFound, UserNotFound, insert_token
from openarchive.services.documents import _load_for_write


class ShareAlreadyExists(Exception):
    """같은 소유자에게 같은 이름의 공유가 이미 있다."""


class ShareNotFound(Exception):
    """공유가 없거나 요청한 사람의 것이 아니다. 둘을 구별하지 않는다."""


async def _owner_user_id(conn: psycopg.AsyncConnection, owner: str) -> UUID:
    row = await (
        await conn.execute("SELECT id FROM users WHERE username = %s", (owner,))
    ).fetchone()
    if row is None:
        raise UserNotFound("사용자를 찾을 수 없습니다.")
    return row[0]


async def _own_share(conn: psycopg.AsyncConnection, share_id: UUID, owner: str) -> None:
    row = await (
        await conn.execute(
            """
            SELECT 1 FROM shares s JOIN users u ON u.id = s.owner_user_id
            WHERE s.id = %s AND u.username = %s
            """,
            (share_id, owner),
        )
    ).fetchone()
    if row is None:
        raise ShareNotFound("공유를 찾을 수 없습니다.")


async def create_share(conn: psycopg.AsyncConnection, *, owner: str, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("공유 이름을 입력하세요.")
    owner_user_id = await _owner_user_id(conn, owner)
    cur = conn.cursor(row_factory=dict_row)
    try:
        await cur.execute(
            """
            INSERT INTO shares (owner_user_id, name) VALUES (%s, %s)
            RETURNING id, name, created_at
            """,
            (owner_user_id, name),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise ShareAlreadyExists("이미 존재하는 공유 이름입니다.") from exc
    share = await cur.fetchone()
    share["documents"] = []
    share["tokens"] = []
    return share


async def list_shares(conn: psycopg.AsyncConnection, *, owner: str) -> list[dict]:
    """자기 공유와 각 공유의 문서·토큰. 토큰은 해시도 원문도 싣지 않는다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT s.id, s.name, s.created_at
        FROM shares s JOIN users u ON u.id = s.owner_user_id
        WHERE u.username = %s
        ORDER BY s.name, s.id
        """,
        (owner,),
    )
    shares = await cur.fetchall()
    share_ids = [share["id"] for share in shares]
    await cur.execute(
        """
        SELECT g.share_id, d.id, d.title
        FROM document_grants g JOIN documents d ON d.id = g.document_id
        WHERE g.share_id = ANY(%s)
        ORDER BY d.title, d.id
        """,
        (share_ids,),
    )
    documents = await cur.fetchall()
    await cur.execute(
        """
        SELECT share_id, id, name, scope, created_at
        FROM api_tokens WHERE share_id = ANY(%s)
        ORDER BY created_at, id
        """,
        (share_ids,),
    )
    tokens = await cur.fetchall()
    for share in shares:
        share["documents"] = [
            {"id": d["id"], "title": d["title"]} for d in documents if d["share_id"] == share["id"]
        ]
        share["tokens"] = [
            {key: t[key] for key in ("id", "name", "scope", "created_at")}
            for t in tokens
            if t["share_id"] == share["id"]
        ]
    return shares


async def delete_share(conn: psycopg.AsyncConnection, share_id: UUID, *, owner: str) -> None:
    """공유를 지우면 부여와 토큰이 CASCADE로 함께 사라진다(026)."""
    cur = await conn.execute(
        """
        DELETE FROM shares s USING users u
        WHERE s.id = %s AND u.id = s.owner_user_id AND u.username = %s
        """,
        (share_id, owner),
    )
    if cur.rowcount == 0:
        raise ShareNotFound("공유를 찾을 수 없습니다.")


async def add_document(
    conn: psycopg.AsyncConnection, share_id: UUID, document_id: UUID, *, owner: str
) -> None:
    """자기 문서만 넣는다. 공개범위(조직 공개·제한)와 무관하게 넣을 수 있고, 두 번 넣어도 한 행이다."""
    await _own_share(conn, share_id, owner)
    await _load_for_write(conn, document_id, owner)
    await conn.execute(
        """
        INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (document_id, share_id),
    )


async def remove_document(
    conn: psycopg.AsyncConnection, share_id: UUID, document_id: UUID, *, owner: str
) -> None:
    await _own_share(conn, share_id, owner)
    await _load_for_write(conn, document_id, owner)
    await conn.execute(
        "DELETE FROM document_grants WHERE document_id = %s AND share_id = %s",
        (document_id, share_id),
    )


async def issue_share_token(
    conn: psycopg.AsyncConnection, share_id: UUID, *, owner: str, name: str
) -> dict:
    """공유 토큰을 발급한다. 원문은 이 응답에 한 번만 나간다(ADR-034)."""
    await _own_share(conn, share_id, owner)
    return await insert_token(conn, user_id=None, share_id=share_id, name=name, scope=SCOPE_READ)


async def revoke_share_token(
    conn: psycopg.AsyncConnection, share_id: UUID, token_id: UUID, *, owner: str
) -> None:
    await _own_share(conn, share_id, owner)
    cur = await conn.execute(
        "DELETE FROM api_tokens WHERE id = %s AND share_id = %s", (token_id, share_id)
    )
    if cur.rowcount == 0:
        raise TokenNotFound("토큰을 찾을 수 없습니다.")
