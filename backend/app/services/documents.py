"""문서 CRUD의 비즈니스 로직. REST 라우터와 MCP 서버가 이 모듈을 공유한다.

HTTP를 알지 못한다 — 실패는 아래 예외로 표현하고, 상태 코드로 옮기는 일은 호출부가 한다.
MCP 서버는 HTTPException을 쓸 수 없으므로 이 경계가 필요하다.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import PurePath
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.services.parsing import (
    UnsupportedFileType,
    detect_content_type,
    extract_text,
    media_type_for,
    needs_ocr,
)
from app.services.visibility import VISIBILITY_VALUES, VISIBLE_TO_USER

# 목록·요약 응답이 쓰는 컬럼. 네 곳에서 같은 나열을 반복하지 않도록 한 곳에 둔다.
SUMMARY_COLUMNS = """id, title, filename, content_type, version, owner_id, visibility, tags,
                     embedding_status, extraction_status, created_at, updated_at"""

# 시연 데이터 최대 추출 텍스트(약 90KB)의 5배보다 크고 DB CHECK와 같은 경계다.
MAX_EXTRACTED_TEXT_LENGTH = 500_000
TEXT_CONTENT_TYPES: tuple[str, ...] = ("txt", "md")
EXTRACTION_FAILED_MESSAGE = "문서에서 텍스트를 추출하지 못했습니다."

# 워커가 추출 결과를 반영한 결과 (ADR-052). applied = 새 텍스트를 썼다, unchanged = 이전 텍스트와
# 같아 완료 표시만 했다, failed = 빈 결과·크기 초과라 인식 실패로 표시했다, skipped = 추출 중인
# 문서가 아니라(이미 끝났거나 삭제됐다) 아무것도 쓰지 않았다.
ExtractionOutcome = Literal["applied", "unchanged", "failed", "skipped"]

SUBJECT_VISIBLE_SQL = f"""
SELECT 1
FROM documents d
WHERE d.id = %(id)s
  AND {VISIBLE_TO_USER}
"""


class DocumentNotFound(Exception):
    """문서가 없거나, 볼 권한이 없어 존재를 알려주지 않는 경우."""


class OriginalFileNotFound(Exception):
    """볼 수 있는 문서지만 요청한 원본 판이 없는 경우. 문서 없음과 구분해도 누출이 없다."""


class DocumentAccessDenied(Exception):
    """문서는 보이지만 수정할 권한이 없는 경우."""


class EmptyExtractedText(Exception):
    """추출 텍스트가 공백뿐인 경우. 저장하면 영원히 검색되지 않는 유령 행이 된다.

    업로드(추출 실패)와 편집(빈 입력)은 원인이 달라 메시지도 다르므로 예외가 문구를 나른다.
    """


class ExtractedTextTooLarge(Exception):
    """추출 텍스트가 서비스와 DB가 허용하는 500KB 경계를 넘은 경우."""


class InvalidVisibility(Exception):
    """공개범위가 열람 술어가 아는 두 값(public, private) 밖인 경우."""


class OriginalFileMissing(Exception):
    """볼 수 있는 문서에 원본이 없어 다시 추출할 대상이 없는 경우."""


class IdempotencyKeyReused(Exception):
    """같은 멱등키에 다른 요청이 온 경우. 재시도가 아니라 키 재사용이다 (ADR-047).

    처음 문서를 돌려주면 호출자는 자기가 보낸 내용이 저장됐다고 믿게 된다.
    """


class _KeyTaken(Exception):
    """동시 요청이 같은 키를 먼저 커밋했다. 이 요청의 트랜잭션을 되돌리는 신호다."""


class ExtractionInProgress(Exception):
    """추출 잡이 끝나지 않은 문서의 텍스트를 바꾸려는 경우 (ADR-052 결정 7).

    워커 결과가 사람이 고친 텍스트를 덮지 않게 한다. 새로고침으로 풀리지 않으므로
    버전 충돌과 구분한다.
    """

    def __init__(self) -> None:
        super().__init__(
            "텍스트를 인식하는 중에는 이 작업을 할 수 없습니다. 인식이 끝난 뒤 다시 시도하세요."
        )


class NoTextToEdit(Exception):
    """텍스트 인식에 실패해 편집할 텍스트가 없는 문서다 (ADR-052 결정 8)."""

    def __init__(self) -> None:
        super().__init__(
            "텍스트를 인식하지 못한 문서라 편집할 텍스트가 없습니다."
            " 원본 파일을 교체하거나 다시 추출하세요."
        )


class RecognitionFailed(Exception):
    """텍스트 인식에 실패한 문서를 재임베딩하려는 경우 (ADR-052 결정 8).

    재임베딩은 `content_hash` 자기 대입으로 003 트리거를 발화시키는데, 그 트리거는
    `extraction_status='done'`일 때만 발화한다(022). 막지 않으면 요청은 성공하고 잡은 생기지
    않는다. 완료로 바꿔 통과시키지 않는 것은 인식 실패 표시가 사라지기 때문이다.
    """

    def __init__(self) -> None:
        super().__init__(
            "텍스트 인식에 실패한 문서는 재임베딩할 수 없습니다."
            " 원본 파일을 교체하거나 다시 추출하세요."
        )


class VersionConflict(Exception):
    """낙관적 동시성 충돌 (ADR-017). 클라이언트가 새로고침할 수 있도록 현재 버전을 함께 전달한다."""

    def __init__(self, current_version: int) -> None:
        super().__init__("다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요.")
        self.current_version = current_version


async def ensure_visible(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str | None = None,
) -> None:
    """주체 문서를 볼 수 없으면 DocumentNotFound를 던진다."""
    # 인터페이스가 아니라 서비스가 주체 문서의 열람 범위를 검증한다. 서비스를 직접
    # 부르는 인터페이스가 늘어도 계약이 약해지지 않게 하기 위함이다.
    row = await (
        await conn.execute(
            SUBJECT_VISIBLE_SQL, {"id": document_id, "user": user_id}
        )
    ).fetchone()
    if row is None:
        raise DocumentNotFound


async def _load_for_write(
    conn: psycopg.AsyncConnection, document_id: UUID, user_id: str
) -> tuple[int, str | None]:
    """쓰기 전 권한을 확인하고 현재 버전과 원본 파일명을 반환한다.

    버전을 여기서 함께 읽어두면 충돌 응답을 만들 때 다시 조회할 필요가 없다.
    조회를 한 번으로 줄이면 그 사이 문서가 사라져 None을 역참조하는 경로도 없어진다.
    `filename`도 같은 이유로 함께 읽는다 — 거절 문구가 이 값으로 갈린다.
    """
    row = await (
        await conn.execute(
            "SELECT owner_id, visibility, version, filename FROM documents WHERE id = %s",
            (document_id,),
        )
    ).fetchone()
    if row is None or (row[1] == "private" and row[0] != user_id):
        raise DocumentNotFound
    if row[0] != user_id:
        raise DocumentAccessDenied
    return row[2], row[3]


async def _lock_for_text_change(
    conn: psycopg.AsyncConnection, document_id: UUID, *, needs_text: bool
) -> dict:
    """텍스트를 바꾸기 전에 문서 행을 잠그고 추출 상태를 판정한다 (ADR-052 결정 7·8).

    권한 확인 **뒤**, 버전 비교 **앞**에 부른다 — 추출 중이면 버전을 맞춰도 풀리지 않으므로
    그 사유가 먼저다. 잠근 뒤 읽은 값으로 판정하므로 워커의 반영(`apply_extracted_text`도
    같은 행을 잠근다)과 경합해도 둘 중 하나만 이긴다. 요청 트랜잭션이 끝날 때까지 잠금이 남는다.

    `needs_text`는 편집·복원처럼 지금 텍스트를 딛는 경로다. 인식에 실패해 텍스트가 한 번도
    없는 문서는 거기서 벗어날 수 없고, 원본 교체나 재추출로만 벗어난다.
    """
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT version, filename, content_hash, extraction_status,
               length(btrim(content, E' \\t\\r\\n\\f')) = 0 AS blank
        FROM documents WHERE id = %s FOR UPDATE
        """,
        (document_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise DocumentNotFound
    if row["extraction_status"] == "pending":
        raise ExtractionInProgress
    if needs_text and row["extraction_status"] == "failed" and row["blank"]:
        raise NoTextToEdit
    return row


async def _latest_original(conn: psycopg.AsyncConnection, document_id: UUID) -> tuple | None:
    return await (
        await conn.execute(
            """
            SELECT filename, data FROM document_files
            WHERE document_id = %s ORDER BY file_version DESC LIMIT 1
            """,
            (document_id,),
        )
    ).fetchone()


def normalize_tags(tags: list[str] | None) -> list[str]:
    """공백을 걷어내고 순서를 보존하며 중복을 제거한다. 대소문자는 구분한다."""
    return list(dict.fromkeys(tag.strip() for tag in (tags or []) if tag.strip()))


def text_label(filename: str | None) -> str:
    """`documents.content`를 사용자에게 부를 이름을 원본 파일 유무로 고른다 (ADR-035 결정 3).

    직접 공급된 텍스트는 무엇에서도 추출된 것이 아니므로 "추출 텍스트"라 부를 수 없다.
    이 함수를 거친 문구는 예외 메시지로 실려 화면에 그대로 표시된다.
    """
    return "추출 텍스트" if filename else "문서 텍스트"


async def create_document(
    conn: psycopg.AsyncConnection,
    *,
    filename: str,
    data: bytes,
    owner_id: str,
    title: str | None = None,
    tags: list[str] | None = None,
    visibility: str = "public",
    idempotency_key: str | None = None,
) -> dict:
    """업로드 파일에서 텍스트를 추출해 문서를 만들고 원본을 1판으로 보관한다 (ADR-046).

    임베딩 잡·텍스트 버전은 트리거가 만든다. 원본은 문서와 같은 트랜잭션에 들어가므로
    문서만 커밋되고 원본이 유실되는 상태가 생기지 않는다 — 호출부가 autocommit 연결을
    넘겨도 이 함수가 트랜잭션을 연다. 멱등키는 `_create_once`를 본다.

    OCR 대상(이미지, 텍스트 레이어가 빈 PDF)은 요청 안에서 인식하지 않는다. 빈 문서 텍스트와
    `extraction_status='pending'`으로 만들면 트리거가 추출 잡을 남기고, 워커가 OCR한 결과를
    `apply_extracted_text`로 반영한다 (ADR-052 결정 3·5). 이때 원본 판은 가리킬 텍스트
    버전이 아직 없어 `text_version`이 NULL이다.
    """
    content_type = detect_content_type(filename)
    content = extract_text(data, content_type)
    extraction_status = "pending" if needs_ocr(content_type, content) else "done"
    if extraction_status == "pending":
        content = ""

    async def insert() -> dict:
        document = await _insert_document(
            conn,
            title=title or PurePath(filename).stem,
            filename=filename,
            content_type=content_type,
            content=content,
            owner_id=owner_id,
            tags=tags,
            visibility=visibility,
            empty_message=EXTRACTION_FAILED_MESSAGE,
            extraction_status=extraction_status,
        )
        await _insert_original_file(
            conn,
            document_id=document["id"],
            file_version=1,
            filename=filename,
            data=data,
            text_version=document["version"] if extraction_status == "done" else None,
            uploaded_by=owner_id,
        )
        return document

    return await _create_once(
        conn,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=_request_hash(
            "file",
            filename=filename,
            data=hashlib.sha256(data).hexdigest(),
            title=title,
            tags=tags,
            visibility=visibility,
        ),
        insert=insert,
    )


def _request_hash(kind: str, **fields: object) -> str:
    """같은 키로 온 요청이 처음 요청과 같은지 가르는 지문. 입구(파일·텍스트)도 포함한다."""
    payload = json.dumps({"kind": kind, **fields}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _replay(
    conn: psycopg.AsyncConnection, *, owner_id: str, key: str, request_hash: str
) -> dict | None:
    """이미 기록된 키면 그 문서의 현재 요약을 돌려준다. 없으면 None이다."""
    row = await (
        await conn.execute(
            "SELECT request_hash, document_id FROM idempotency_keys"
            " WHERE owner_id = %s AND key = %s",
            (owner_id, key),
        )
    ).fetchone()
    if row is None:
        return None
    if row[0] != request_hash:
        raise IdempotencyKeyReused("같은 Idempotency-Key로 다른 요청을 보낼 수 없습니다.")
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(f"SELECT {SUMMARY_COLUMNS} FROM documents WHERE id = %s", (row[1],))
    return await cur.fetchone()


async def _create_once(
    conn: psycopg.AsyncConnection,
    *,
    owner_id: str,
    idempotency_key: str | None,
    request_hash: str,
    insert: Callable[[], Awaitable[dict]],
) -> dict:
    """`insert`와 키 기록을 트랜잭션 블록 하나로 묶는다 (ADR-047).

    호출부가 이미 트랜잭션 안이면(API는 인증 조회가 연다) 블록은 SAVEPOINT가 되고 커밋은
    호출부가 연결을 반납할 때다. 어느 쪽이든 문서와 키는 함께 커밋되거나 함께 되돌려진다.

    같은 키가 이미 있으면 `insert` 없이 처음 문서를 돌려준다. 동시 요청은 기본키가
    직렬화한다 — 키 INSERT가 앞 트랜잭션의 끝을 기다렸다가, 앞이 커밋했으면 아무것도
    넣지 않는다. 그러면 이 블록(문서 행 포함)을 되돌리고 커밋된 행을 읽는다. 앞이
    롤백했으면 키가 들어가 이 요청이 처음 요청이 된다.
    """
    if idempotency_key is None:
        async with conn.transaction():
            return await insert()
    while True:
        existing = await _replay(
            conn, owner_id=owner_id, key=idempotency_key, request_hash=request_hash
        )
        if existing is not None:
            return existing
        try:
            async with conn.transaction():
                document = await insert()
                recorded = await conn.execute(
                    """
                    INSERT INTO idempotency_keys (owner_id, key, request_hash, document_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING 1
                    """,
                    (owner_id, idempotency_key, request_hash, document["id"]),
                )
                if await recorded.fetchone() is None:
                    raise _KeyTaken
        except _KeyTaken:
            # 다시 돌면 _replay가 커밋된 행을 찾는다. 그 사이 문서가 지워졌다면 키도
            # 함께 지워졌으므로(CASCADE) 새로 만든다.
            continue
        return document


async def _insert_original_file(
    conn: psycopg.AsyncConnection,
    *,
    document_id: UUID,
    file_version: int,
    filename: str,
    data: bytes,
    text_version: int | None,
    uploaded_by: str,
) -> None:
    """원본 파일 한 판을 넣는다. 크기와 sha256은 DB가 data에서 계산한다.

    바이트는 `%b`(이진 포맷)로 보낸다. 텍스트 포맷이면 hex 인코딩으로 크기가 두 배가
    되어, 상한 50MB 파일이 100MB로 OpenProxy를 지난다.
    """
    await conn.execute(
        """
        INSERT INTO document_files
            (document_id, file_version, filename, data, text_version, uploaded_by)
        VALUES (%s, %s, %s, %b, %s, %s)
        """,
        (document_id, file_version, filename, data, text_version, uploaded_by),
    )


async def create_text_document(
    conn: psycopg.AsyncConnection,
    *,
    title: str,
    content: str,
    content_type: str = "md",
    owner_id: str,
    tags: list[str] | None = None,
    visibility: str = "public",
    idempotency_key: str | None = None,
) -> dict:
    """공급자가 이미 가진 텍스트로 문서를 만든다. 원본 파일이 없으므로 filename은 NULL이다."""
    # REST는 pydantic Literal이 먼저 422로 막아 이 가드에 닿지 않는다. 그래도 두는 것은
    # MCP 서버와 스크립트가 라우터를 거치지 않고 이 함수를 직접 부르기 때문이다 — 코어가
    # 자기 계약을 스스로 지킨다.
    if content_type not in TEXT_CONTENT_TYPES:
        raise UnsupportedFileType("텍스트로 공급할 수 있는 유형은 txt, md입니다.")

    async def insert() -> dict:
        return await _insert_document(
            conn,
            title=title,
            filename=None,
            content_type=content_type,
            content=content,
            owner_id=owner_id,
            tags=tags,
            visibility=visibility,
            empty_message="문서 텍스트는 비어 있을 수 없습니다.",
        )

    return await _create_once(
        conn,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=_request_hash(
            "text",
            title=title,
            content=content,
            content_type=content_type,
            tags=tags,
            visibility=visibility,
        ),
        insert=insert,
    )


async def _insert_document(
    conn: psycopg.AsyncConnection,
    *,
    title: str,
    filename: str | None,
    content_type: str,
    content: str,
    owner_id: str,
    tags: list[str] | None,
    visibility: str,
    empty_message: str,
    extraction_status: str = "done",
) -> dict:
    """검증된 문서 텍스트를 저장한다. 파생 데이터는 DB 트리거가 만든다.

    빈 텍스트 문구만 인자로 받는다 — 업로드는 추출 실패고 직접 공급은 빈 입력이라
    상황 자체가 다르다. 크기 초과는 두 경로가 같은 상황이므로 `text_label`로 부른다.
    추출 중(`pending`) 문서는 빈 텍스트로 들어가며 텍스트 검증을 건너뛴다 — 그 텍스트는
    `apply_extracted_text`가 검증한다.
    """
    if extraction_status == "done" and not content.strip():
        raise EmptyExtractedText(empty_message)
    if len(content) > MAX_EXTRACTED_TEXT_LENGTH:
        raise ExtractedTextTooLarge(
            f"{text_label(filename)}는 500KB를 넘을 수 없습니다."
        )
    if visibility not in VISIBILITY_VALUES:
        raise InvalidVisibility("공개범위는 public, private 중 하나여야 합니다.")

    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        INSERT INTO documents
            (title, filename, content_type, content, content_hash, owner_id, visibility, tags,
             extraction_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {SUMMARY_COLUMNS}
        """,
        (
            title,
            filename,
            content_type,
            content,
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
            owner_id,
            visibility,
            normalize_tags(tags),
            extraction_status,
        ),
    )
    return await cur.fetchone()


async def apply_extracted_text(
    conn: psycopg.AsyncConnection, document_id: UUID, content: str
) -> ExtractionOutcome:
    """워커가 OCR한 결과를 추출 중인 문서에 반영한다 (ADR-052 결정 5·6·8). 권한 검사는 하지 않는다.

    문서 행을 `FOR UPDATE`로 잠근 뒤 판정한다. 워커는 자기 잡 소유 확인과 같은 트랜잭션
    안에서 부른다 — 호출자가 트랜잭션을 열었으면 이 블록은 SAVEPOINT가 된다.

    본문·`content_hash`·`extraction_status='done'`은 반드시 한 UPDATE에서 쓴다. 003 트리거는
    `UPDATE OF content_hash`에서 `NEW.extraction_status = 'done'`일 때만 발화하므로(022),
    나눠 쓰면 텍스트 버전·임베딩 잡이 조용히 생기지 않는다.

    빈 결과·크기 초과는 예외 없이 `failed`로 표시만 한다 — 결정적이라 워커가 재시도해도
    같은 결과이고, 문서 텍스트(새 문서면 빈 텍스트, 재추출이면 이전 텍스트)는 그대로 둔다.
    """
    async with conn.transaction():
        row = await (
            await conn.execute(
                "SELECT content, content_hash, extraction_status FROM documents"
                " WHERE id = %s FOR UPDATE",
                (document_id,),
            )
        ).fetchone()
        if row is None or row[2] != "pending":
            return "skipped"
        current_content, current_hash, _ = row

        if not content.strip() or len(content) > MAX_EXTRACTED_TEXT_LENGTH:
            await conn.execute(
                "UPDATE documents SET extraction_status = 'failed', updated_at = now()"
                " WHERE id = %s",
                (document_id,),
            )
            return "failed"

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_hash == current_hash:
            # content_hash를 SET 절에 언급만 해도 트리거가 발화해(003) 같은 내용의 텍스트
            # 버전과 재임베딩이 생긴다. 완료 표시만 한다.
            await conn.execute(
                "UPDATE documents SET extraction_status = 'done', updated_at = now()"
                " WHERE id = %s",
                (document_id,),
            )
            return "unchanged"

        # 첫 추출(빈 텍스트)은 v1이 첫 텍스트다 — 버전을 올리지 않는다. 재추출은 편집처럼 올린다.
        first = not current_content.strip()
        updated = await (
            await conn.execute(
                """
                UPDATE documents
                   SET version = version + %(bump)s, content = %(content)s,
                       content_hash = %(hash)s, extraction_status = 'done', updated_at = now()
                 WHERE id = %(id)s
                RETURNING version
                """,
                {
                    "id": document_id,
                    "bump": 0 if first else 1,
                    "content": content,
                    "hash": content_hash,
                },
            )
        ).fetchone()
        # 워커가 읽은 최신 원본 판만 방금 기록된 텍스트 버전을 가리키게 한다. 인식에 실패한 채
        # 교체된 이전 판은 텍스트를 낸 적이 없으므로 NULL로 남는다.
        await conn.execute(
            """
            UPDATE document_files SET text_version = %(version)s
             WHERE document_id = %(id)s AND text_version IS NULL
               AND file_version = (SELECT max(file_version) FROM document_files
                                    WHERE document_id = %(id)s)
            """,
            {"version": updated[0], "id": document_id},
        )
    return "applied"


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

    # 원본 판은 메타데이터만 싣는다. 상세는 화면이 수시로 부르는 응답이라 바이트를 섞지 않는다.
    await cur.execute(
        """
        SELECT file_version, filename, size, sha256, text_version, uploaded_by, uploaded_at
        FROM document_files
        WHERE document_id = %s
        ORDER BY file_version
        """,
        (document_id,),
    )
    document["files"] = await cur.fetchall()
    return document


async def get_original_file(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str | None,
    file_version: int | None = None,
) -> dict:
    """원본 한 판의 바이트를 돌려준다. `file_version`이 없으면 최신 판이다.

    열람 검증을 먼저 한다 — 볼 수 없는 문서는 원본 유무와 관계없이 DocumentNotFound라
    원본의 존재가 문서의 존재를 누출하지 않는다 (ADR-027).
    """
    await ensure_visible(conn, document_id, user_id=user_id)
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT filename, data, sha256
        FROM document_files
        WHERE document_id = %(id)s
          AND (%(version)s::int IS NULL OR file_version = %(version)s)
        ORDER BY file_version DESC
        LIMIT 1
        """,
        {"id": document_id, "version": file_version},
    )
    original = await cur.fetchone()
    if original is None:
        raise OriginalFileNotFound
    original["media_type"] = media_type_for(original["filename"])
    return original


async def update_extracted_text(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str,
    content: str,
    client_version: int,
) -> dict:
    """문서 텍스트를 낙관적 동시성으로 갱신한다 (ADR-017).

    편집 대상은 문서 텍스트이며 원본 파일이 아니다. 원본 파일은 편집하지 않는다(판으로 따로 보관된다).
    업로드로 들어온 문서에서는 그 텍스트가 추출 텍스트이고, 직접 공급된 문서
    (`filename IS NULL`)에는 추출한 대상이 없다 — 거절 문구가 그 구분을 따른다
    (ADR-035 결정 3).
    """
    await _load_for_write(conn, document_id, user_id)
    locked = await _lock_for_text_change(conn, document_id, needs_text=True)
    return await _write_text(
        conn,
        document_id,
        content=content,
        client_version=client_version,
        current_version=locked["version"],
        filename=locked["filename"],
    )


async def _write_text(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    content: str,
    client_version: int,
    current_version: int,
    filename: str | None,
) -> dict:
    """문서 텍스트 갱신의 본체. 권한은 호출부가 이미 확인했다고 가정한다.

    편집·복원·재추출·원본 교체가 모두 이 한 UPDATE를 지난다 — 텍스트를 바꾸는 경로가 둘이 되면
    한쪽만 고쳐지는 자리가 생긴다.

    `extraction_status='done'`을 같은 UPDATE에서 쓴다 — 003 트리거는 새 값이 `done`일 때만
    발화하므로(022) 인식 실패 문서를 고치거나 교체해도 텍스트 버전·잡이 빠지지 않는다. 추출 중인
    문서는 호출부가 `_lock_for_text_change`로 이미 막았다. 텍스트가 한 번도 없던 문서(인식 실패한
    새 스캔)의 첫 텍스트는 워커의 첫 추출처럼 v1이다 — 버전을 올리지 않는다.
    """
    label = text_label(filename)
    if not content.strip():
        raise EmptyExtractedText(f"{label}는 비어 있을 수 없습니다.")
    if len(content) > MAX_EXTRACTED_TEXT_LENGTH:
        raise ExtractedTextTooLarge(f"{label}는 500KB를 넘을 수 없습니다.")
    if current_version != client_version:
        raise VersionConflict(current_version)

    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        UPDATE documents
           SET version = version
                         + CASE WHEN btrim(content, E' \\t\\r\\n\\f') = '' THEN 0 ELSE 1 END,
               content = %(content)s, content_hash = %(hash)s,
               extraction_status = 'done', updated_at = now()
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


async def replace_original_file(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str,
    filename: str,
    data: bytes,
    client_version: int,
) -> dict:
    """새 원본 파일을 새 판으로 쌓고, 추출 텍스트가 달라졌으면 새 텍스트 버전을 만든다 (ADR-046).

    이전 판은 지우지도 덮지도 않는다 — 덮으면 교체가 비보관이 만들던 원본 유실을 되살린다.
    문서의 정체성(id·제목·태그·링크·관계·공개범위)은 그대로이고 파일명·유형만 바뀐다.
    텍스트 버전·잡은 `documents` 트리거가 만든다. 낙관적 잠금은 편집과 같은 규칙이다 (ADR-017).
    """
    await _load_for_write(conn, document_id, user_id)
    locked = await _lock_for_text_change(conn, document_id, needs_text=False)
    current_version = locked["version"]
    if current_version != client_version:
        raise VersionConflict(current_version)

    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT sha256 FROM document_files
        WHERE document_id = %s ORDER BY file_version DESC LIMIT 1
        """,
        (document_id,),
    )
    latest = await cur.fetchone()
    if latest is not None and latest["sha256"] == hashlib.sha256(data).hexdigest():
        # 같은 파일을 다시 올린 것이다. 새 판도 새 텍스트 버전도 만들지 않는다.
        await cur.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM documents WHERE id = %s", (document_id,)
        )
        return await cur.fetchone()

    content_type = detect_content_type(filename)
    content = extract_text(data, content_type)
    ocr = needs_ocr(content_type, content)
    if not ocr and not content.strip():
        raise EmptyExtractedText(EXTRACTION_FAILED_MESSAGE)
    # OCR 대상이면 텍스트를 쓰지 않는다 — 요청 안에서 인식하지 않고 추출 잡으로 넘긴다
    # (ADR-052 결정 3·6). 인식이 끝날 때까지 이전 텍스트·청크로 검색된다.
    text_changed = (
        not ocr
        and hashlib.sha256(content.encode("utf-8")).hexdigest() != locked["content_hash"]
    )

    async with conn.transaction():
        expected_version = client_version
        if text_changed:
            # 텍스트가 바뀌면 편집과 같은 한 UPDATE를 지난다 — 낙관적 잠금·길이 검증·
            # 트리거 발화가 편집과 같다. 같으면 content_hash를 언급하지 않는다. 값이 같아도
            # 언급만으로 트리거가 발화해(003) 같은 내용의 텍스트 버전과 재임베딩이 생긴다.
            written = await _write_text(
                conn,
                document_id,
                content=content,
                client_version=client_version,
                current_version=current_version,
                filename=filename,
            )
            expected_version = written["version"]
        await cur.execute(
            f"""
            UPDATE documents
               SET filename = %(filename)s, content_type = %(content_type)s,
                   extraction_status = %(extraction_status)s, updated_at = now()
             WHERE id = %(id)s AND version = %(version)s
            RETURNING {SUMMARY_COLUMNS}
            """,
            {
                "id": document_id,
                "version": expected_version,
                "filename": filename,
                "content_type": content_type,
                # pending으로 바뀌면 022 트리거가 추출 잡을 만든다. 텍스트 레이어가 있는 원본으로
                # 바꾼 인식 실패 문서는 여기서 done이 된다. content_hash는 언급하지 않는다(003).
                "extraction_status": "pending" if ocr else "done",
            },
        )
        document = await cur.fetchone()
        if document is None:
            # 권한 확인과 UPDATE 사이에 다른 트랜잭션이 커밋된 경우다.
            raise VersionConflict(await _current_version(conn, document_id))

        # 위 UPDATE가 문서 행을 잠근 뒤에 판 번호를 정하므로 동시 교체가 같은 번호를 얻지 않는다.
        row = await (
            await conn.execute(
                "SELECT coalesce(max(file_version), 0) + 1 FROM document_files WHERE document_id = %s",
                (document_id,),
            )
        ).fetchone()
        await _insert_original_file(
            conn,
            document_id=document_id,
            file_version=row[0],
            filename=filename,
            data=data,
            # 인식을 기다리는 판은 아직 자기 텍스트가 없다. 지금 텍스트가 있으면 그것을, 한 번도
            # 없던 문서면 NULL을 가리키고, 워커가 첫 텍스트를 쓸 때 채운다.
            text_version=None if ocr and locked["blank"] else document["version"],
            uploaded_by=user_id,
        )
    return document


async def reextract_text(
    conn: psycopg.AsyncConnection, document_id: UUID, *, expected_version: int
) -> tuple[dict, bool]:
    """보관된 최신 원본에서 텍스트를 다시 뽑는다. 권한 검사는 하지 않는다.

    결과가 현재 텍스트와 같으면 아무것도 쓰지 않는다 — `content_hash`를 언급만 해도
    트리거가 발화해(003) 같은 내용의 텍스트 버전과 재임베딩이 생긴다. 다르면 편집 경로를
    그대로 지나므로 낙관적 잠금·길이 검증·트리거 발화가 편집과 같다. 원본 판은 만들지 않는다.

    최신 원본이 OCR 대상(이미지, 텍스트 레이어가 빈 PDF)이면 텍스트를 쓰지 않고 추출 중으로
    바꿔 워커에 넘긴다 — 돌려주는 문서의 `extraction_status`가 `pending`이다 (ADR-052 결정 6).
    """
    locked = await _lock_for_text_change(conn, document_id, needs_text=False)
    current_version = locked["version"]
    # 사람이 고친 텍스트를 덮을 수 있으므로, 결과와 무관하게 호출자가 본 버전이어야 한다.
    if current_version != expected_version:
        raise VersionConflict(current_version)

    original = await _latest_original(conn, document_id)
    if original is None:
        raise OriginalFileMissing

    content_type = detect_content_type(original[0])
    content = extract_text(original[1], content_type)
    cur = conn.cursor(row_factory=dict_row)
    if needs_ocr(content_type, content):
        # 요청 안에서 OCR하지 않는다 (ADR-052 결정 3·6). 이전 텍스트는 그대로 두고 추출 중으로
        # 바꾸면 022 트리거가 추출 잡을 만든다. content_hash를 언급하지 않는다(003).
        # 인식에 실패했던 문서는 이것이 재시도 경로다.
        await cur.execute(
            f"""
            UPDATE documents SET extraction_status = 'pending', updated_at = now()
             WHERE id = %s
            RETURNING {SUMMARY_COLUMNS}, content
            """,
            (document_id,),
        )
        return await cur.fetchone(), False
    if not content.strip():
        raise EmptyExtractedText(EXTRACTION_FAILED_MESSAGE)
    if hashlib.sha256(content.encode("utf-8")).hexdigest() == locked["content_hash"]:
        # 인식 실패 표시가 남아 있었다면 지금 원본에서 같은 텍스트를 얻었으니 끝난 것이다.
        # 워커의 unchanged와 같다 — content_hash는 언급하지 않는다(003).
        await cur.execute(
            f"""
            UPDATE documents SET extraction_status = 'done' WHERE id = %s
            RETURNING {SUMMARY_COLUMNS}, content
            """,
            (document_id,),
        )
        return await cur.fetchone(), False

    document = await _write_text(
        conn,
        document_id,
        content=content,
        client_version=expected_version,
        current_version=current_version,
        filename=locked["filename"],
    )
    return document, True


async def reextract_document(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str,
    client_version: int,
) -> tuple[dict, bool]:
    """사용자 경로의 재추출. 소유자만 할 수 있다 — 편집과 같은 쓰기 권한이다."""
    await _load_for_write(conn, document_id, user_id)
    return await reextract_text(conn, document_id, expected_version=client_version)


async def get_document_version(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    version: int,
    user_id: str,
) -> dict:
    """과거 텍스트 버전의 본문을 돌려준다 (ADR-037 결정 1).

    열람 범위는 문서 본체와 같은 술어를 쓴다. 볼 수 없는 문서의 버전은 없는 것과 같이
    다뤄야 한다 — 버전 번호의 존재만 알려줘도 문서의 존재와 수정 횟수가 새어 나간다.
    """
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        SELECT v.version, v.content, v.created_at
        FROM document_versions v
        JOIN documents d ON d.id = v.document_id
        WHERE v.document_id = %(id)s
          AND v.version = %(version)s
          AND {VISIBLE_TO_USER}
        """,
        {"id": document_id, "version": version, "user": user_id},
    )
    document_version = await cur.fetchone()
    if document_version is None:
        raise DocumentNotFound
    return document_version


async def restore_version(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    version: int,
    user_id: str,
    client_version: int,
) -> dict:
    """과거 버전의 본문으로 **새 텍스트 버전을 만든다** — 되감기가 아니다 (ADR-037 결정 2).

    편집과 같은 `_write_text`를 지난다. 낙관적 잠금·길이 검증·`content_hash` 갱신·트리거 발화가
    편집과 완전히 같아야 하고, 여기에 별도 UPDATE를 두면 한쪽만 고쳐지는 자리가 생긴다.
    `version`을 감소시키거나 이력 행을 지우지 않으므로 정합성 검증 쿼리의 기준도 그대로다.
    """
    await _load_for_write(conn, document_id, user_id)
    # 과거 버전을 찾기 전에 판정한다 — 인식에 실패한 새 스캔에는 과거 버전이 없어, 순서가
    # 바뀌면 벗어나는 방법을 알려주는 409 대신 404가 나간다.
    locked = await _lock_for_text_change(conn, document_id, needs_text=True)
    past = await get_document_version(
        conn, document_id, version=version, user_id=user_id
    )
    return await _write_text(
        conn,
        document_id,
        content=past["content"],
        client_version=client_version,
        current_version=locked["version"],
        filename=locked["filename"],
    )


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
    트리거를 발화시켜 잡 생성과 코얼레싱을 DB에 맡긴다. 그 트리거는 추출이 끝난 문서에서만
    발화하므로(022) 추출 중·인식 실패 문서는 409로 막는다 — 막지 않으면 조용한 무동작이다.
    """
    await _load_for_write(conn, document_id, user_id)
    locked = await _lock_for_text_change(conn, document_id, needs_text=False)
    if locked["extraction_status"] == "failed":
        raise RecognitionFailed
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
