"""문서 CRUD의 비즈니스 로직. REST 라우터와 MCP 서버가 이 모듈을 공유한다.

HTTP를 알지 못한다 — 실패는 아래 예외로 표현하고, 상태 코드로 옮기는 일은 호출부가 한다.
MCP 서버는 HTTPException을 쓸 수 없으므로 이 경계가 필요하다.
"""

import hashlib
from pathlib import PurePath
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.services.parsing import detect_content_type, extract_text
from app.services.visibility import VISIBLE_TO_USER

# 목록·요약 응답이 쓰는 컬럼. 네 곳에서 같은 나열을 반복하지 않도록 한 곳에 둔다.
SUMMARY_COLUMNS = """id, title, filename, content_type, version, owner_id, visibility, tags,
                     embedding_status, created_at, updated_at"""

# 시연 데이터 최대 추출 텍스트(약 90KB)의 5배보다 크고 DB CHECK와 같은 경계다.
MAX_EXTRACTED_TEXT_LENGTH = 500_000


class DocumentNotFound(Exception):
    """문서가 없거나, 볼 권한이 없어 존재를 알려주지 않는 경우."""


class DocumentAccessDenied(Exception):
    """문서는 보이지만 수정할 권한이 없는 경우."""


class EmptyExtractedText(Exception):
    """추출 텍스트가 공백뿐인 경우. 저장하면 영원히 검색되지 않는 유령 행이 된다.

    업로드(추출 실패)와 편집(빈 입력)은 원인이 달라 메시지도 다르므로 예외가 문구를 나른다.
    """


class ExtractedTextTooLarge(Exception):
    """추출 텍스트가 서비스와 DB가 허용하는 500KB 경계를 넘은 경우."""


class VersionConflict(Exception):
    """낙관적 동시성 충돌 (ADR-017). 클라이언트가 새로고침할 수 있도록 현재 버전을 함께 전달한다."""

    def __init__(self, current_version: int) -> None:
        super().__init__("다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요.")
        self.current_version = current_version


async def _load_for_write(
    conn: psycopg.AsyncConnection, document_id: UUID, user_id: str
) -> int:
    """쓰기 전 권한을 확인하고 현재 버전을 반환한다.

    버전을 여기서 함께 읽어두면 충돌 응답을 만들 때 다시 조회할 필요가 없다.
    조회를 한 번으로 줄이면 그 사이 문서가 사라져 None을 역참조하는 경로도 없어진다.
    """
    row = await (
        await conn.execute(
            "SELECT owner_id, visibility, version FROM documents WHERE id = %s",
            (document_id,),
        )
    ).fetchone()
    if row is None or (row[1] == "private" and row[0] != user_id):
        raise DocumentNotFound
    if row[0] != user_id:
        raise DocumentAccessDenied
    return row[2]


def normalize_tags(tags: list[str] | None) -> list[str]:
    """공백을 걷어내고 순서를 보존하며 중복을 제거한다. 대소문자는 구분한다."""
    return list(dict.fromkeys(tag.strip() for tag in (tags or []) if tag.strip()))


async def create_document(
    conn: psycopg.AsyncConnection,
    *,
    filename: str,
    data: bytes,
    owner_id: str,
    title: str | None = None,
    tags: list[str] | None = None,
    visibility: str = "public",
) -> dict:
    """업로드 파일에서 텍스트를 추출해 문서를 만든다. 임베딩 잡은 트리거가 만든다."""
    content_type = detect_content_type(filename)
    content = extract_text(data, content_type)
    if not content.strip():
        raise EmptyExtractedText(
            "문서에서 텍스트를 추출하지 못했습니다. 스캔 이미지 PDF는 지원하지 않습니다."
        )
    if len(content) > MAX_EXTRACTED_TEXT_LENGTH:
        raise ExtractedTextTooLarge("추출 텍스트는 500KB를 넘을 수 없습니다.")

    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        INSERT INTO documents
            (title, filename, content_type, content, content_hash, owner_id, visibility, tags)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {SUMMARY_COLUMNS}
        """,
        (
            title or PurePath(filename).stem,
            filename,
            content_type,
            content,
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
            owner_id,
            visibility,
            normalize_tags(tags),
        ),
    )
    return await cur.fetchone()


async def list_documents(
    conn: psycopg.AsyncConnection,
    *,
    user_id: str | None = None,
    embedding_status: str | None = None,
    tag: str | None = None,
) -> list[dict]:
    """권한 술어와 선택적 필터를 한 쿼리로 적용한다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        SELECT {SUMMARY_COLUMNS}
        FROM documents d
        WHERE {VISIBLE_TO_USER}
          AND (%(status)s::text IS NULL OR embedding_status = %(status)s)
          AND (%(tag)s::text IS NULL OR %(tag)s = ANY(tags))
        ORDER BY created_at DESC, id
        """,
        {"user": user_id, "status": embedding_status, "tag": tag},
    )
    return await cur.fetchall()


async def get_document(
    conn: psycopg.AsyncConnection, document_id: UUID, *, user_id: str | None = None
) -> dict:
    """문서 상세와 텍스트 버전 이력을 반환한다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        SELECT {SUMMARY_COLUMNS}, content,
               (SELECT count(*) FROM document_chunks c WHERE c.document_id = d.id) AS chunk_count,
               (SELECT min(version) FROM document_chunks c WHERE c.document_id = d.id)
                   AS chunk_version
        FROM documents d
        WHERE id = %(id)s
          AND {VISIBLE_TO_USER}
        """,
        {"id": document_id, "user": user_id},
    )
    document = await cur.fetchone()
    if document is None:
        raise DocumentNotFound

    await cur.execute(
        """
        SELECT version, created_at
        FROM document_versions
        WHERE document_id = %s
        ORDER BY version
        """,
        (document_id,),
    )
    document["versions"] = await cur.fetchall()
    return document


async def update_extracted_text(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str,
    content: str,
    client_version: int,
) -> dict:
    """추출 텍스트를 낙관적 동시성으로 갱신한다 (ADR-017).

    편집 대상은 추출 텍스트이며 원본 파일이 아니다. 원본 파일은 보관하지 않는다.
    """
    current_version = await _load_for_write(conn, document_id, user_id)
    if not content.strip():
        raise EmptyExtractedText("추출 텍스트는 비어 있을 수 없습니다.")
    if len(content) > MAX_EXTRACTED_TEXT_LENGTH:
        raise ExtractedTextTooLarge("추출 텍스트는 500KB를 넘을 수 없습니다.")
    if current_version != client_version:
        raise VersionConflict(current_version)

    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        UPDATE documents
           SET version = version + 1, content = %(content)s, content_hash = %(hash)s,
               updated_at = now()
         WHERE id = %(id)s AND version = %(client_version)s
        RETURNING {SUMMARY_COLUMNS}, content
        """,
        {
            "id": document_id,
            "content": content,
            "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "client_version": client_version,
        },
    )
    document = await cur.fetchone()
    if document is None:
        # 권한 확인과 UPDATE 사이에 다른 트랜잭션이 커밋된 경우다.
        raise VersionConflict(await _current_version(conn, document_id))
    return document


async def update_tags(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str,
    tags: list[str],
) -> dict:
    """태그만 교체한다. 추출 텍스트 버전과 임베딩 파이프라인은 건드리지 않는다."""
    await _load_for_write(conn, document_id, user_id)
    normalized_tags = normalize_tags(tags)

    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        UPDATE documents SET tags = %(tags)s, updated_at = now() WHERE id = %(id)s
        RETURNING {SUMMARY_COLUMNS}
        """,
        {"id": document_id, "tags": normalized_tags},
    )
    document = await cur.fetchone()
    if document is None:
        raise DocumentNotFound
    return document


async def _current_version(conn: psycopg.AsyncConnection, document_id: UUID) -> int:
    row = await (
        await conn.execute("SELECT version FROM documents WHERE id = %s", (document_id,))
    ).fetchone()
    if row is None:
        raise DocumentNotFound
    return row[0]


async def delete_document(
    conn: psycopg.AsyncConnection, document_id: UUID, *, user_id: str
) -> None:
    await _load_for_write(conn, document_id, user_id)
    await conn.execute("DELETE FROM documents WHERE id = %s", (document_id,))


async def request_reembedding(
    conn: psycopg.AsyncConnection, document_id: UUID, *, user_id: str
) -> dict:
    """재임베딩을 요청한다.

    앱에서 embedding_jobs에 INSERT하지 않는다 (CLAUDE.md CRITICAL). 자기 대입 UPDATE로
    트리거를 발화시켜 잡 생성과 코얼레싱을 DB에 맡긴다.
    """
    await _load_for_write(conn, document_id, user_id)
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        UPDATE documents SET content_hash = content_hash WHERE id = %s
        RETURNING {SUMMARY_COLUMNS}
        """,
        (document_id,),
    )
    document = await cur.fetchone()
    if document is None:
        raise DocumentNotFound
    return document
