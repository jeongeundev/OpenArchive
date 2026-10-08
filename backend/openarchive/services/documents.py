"""문서 CRUD의 비즈니스 로직. REST 라우터와 MCP 서버가 이 모듈을 공유한다.

HTTP를 알지 못한다 — 실패는 아래 예외로 표현하고, 상태 코드로 옮기는 일은 호출부가 한다.
MCP 서버는 HTTPException을 쓸 수 없으므로 이 경계가 필요하다.
"""

import difflib
import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import PurePath
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from openarchive.services.chunking import chunk_spans
from openarchive.services.grants import insert_grants, resolve_grantees
from openarchive.services.parsing import (
    UnsupportedFileType,
    detect_content_type,
    extract_text,
    media_type_for,
    needs_ocr,
)
from openarchive.services.visibility import (
    FOLDER_VISIBLE_TO_USER,
    NOT_TRASHED,
    VISIBILITY_VALUES,
    VISIBLE_TO_USER,
    root_folder_visibility,
)

# 실제로 적용되는 공개범위. 「폴더 범위 따름」 문서는 최상위 폴더의 값이다 — 폴더로 만든 문서의
# 자기 visibility는 private로 닫혀 있어(ADR-054), 그대로 보이면 조직 공개 폴더 안 문서가 「제한」으로
# 보인다. 거슬러 오르기는 열람 술어와 같은 조각이다(visibility.py). 볼 수 없는 폴더의 최상위는 늘
# private라 새로 드러나는 것이 없다. FROM 별칭 없이 쓰이는 RETURNING에도 들어가므로 바깥 컬럼은
# 한정하지 않는다 — folders에는 folder_id·follows_folder 컬럼이 없어 바깥 행으로 해석된다.
EFFECTIVE_VISIBILITY = (
    "CASE WHEN folder_id IS NOT NULL AND follows_folder THEN "
    + root_folder_visibility("folder_id")
    + " ELSE visibility END AS effective_visibility"
)

# 목록·요약 응답이 쓰는 컬럼. 네 곳에서 같은 나열을 반복하지 않도록 한 곳에 둔다.
SUMMARY_COLUMNS = """id, title, filename, content_type, version, owner_id, visibility, tags,
                     embedding_status, extraction_status, created_at, updated_at, """ + (
    EFFECTIVE_VISIBILITY
)

# 시연 데이터 최대 추출 텍스트(약 90KB)의 5배보다 크고 DB CHECK와 같은 경계다.
MAX_EXTRACTED_TEXT_LENGTH = 500_000
PREVIEWABLE_EXTENSIONS: frozenset[str] = frozenset({"pdf", "png", "jpg", "jpeg"})
TEXT_CONTENT_TYPES: tuple[str, ...] = ("txt", "md")
EXTRACTION_FAILED_MESSAGE = "문서에서 텍스트를 추출하지 못했습니다."

# 워커가 추출 결과를 반영한 결과 (ADR-052). applied = 새 텍스트를 썼다, unchanged = 이전 텍스트와
# 같아 완료 표시만 했다, failed = 빈 결과·크기 초과라 인식 실패로 표시했다, skipped = 추출 중인
# 문서가 아니라(이미 끝났거나 삭제됐다) 아무것도 쓰지 않았다.
ExtractionOutcome = Literal["applied", "unchanged", "failed", "skipped"]
ExtractionStatus = Literal["pending", "done", "failed"]

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


class OriginalNotPreviewable(Exception):
    """원본 판의 확장자가 미리보기 허용 목록 밖인 경우."""


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


class GrantsOnPublicDocument(Exception):
    """조직 공개 문서에 부여 대상을 보낸 경우 (ADR-044 관리 경로 결정 4).

    효력 없는 부여 행이라는 숨은 상태를 남기지 않는다. visibility를 조용히 바꾸지도 않는다.
    """

    def __init__(self) -> None:
        super().__init__(
            "조직 공개 문서에는 부여 대상이 필요 없습니다."
            " 대상에게만 열려면 visibility=private로 보내세요."
        )


class GrantToOwner(Exception):
    """소유자 자신을 부여 대상으로 보낸 경우 (ADR-044 관리 경로 결정 4).

    소유자는 부여 없이도 본다. 효력 없는 부여 행이라는 숨은 상태를 남기지 않는다.
    """

    def __init__(self) -> None:
        super().__init__("소유자는 부여 없이도 이 문서를 봅니다. 부여 대상에서 소유자를 빼세요.")


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
            f"""
            SELECT owner_id, version, filename FROM documents d
            WHERE id = %(id)s AND {VISIBLE_TO_USER}
            """,
            {"id": document_id, "user": user_id},
        )
    ).fetchone()
    if row is None:
        raise DocumentNotFound
    if row[0] != user_id:
        raise DocumentAccessDenied
    return row[1], row[2]


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


def _creation_scope(folder_id, visibility, users, groups):
    if folder_id is not None:
        if visibility is not None or users is not None or groups is not None:
            raise ValueError("폴더에 넣는 문서는 폴더의 열람 범위를 따릅니다. 개별 지정은 문서 상세에서 합니다.")
        return "private"
    return "public" if visibility is None else visibility


async def create_document(
    conn: psycopg.AsyncConnection,
    *,
    filename: str,
    data: bytes,
    owner_id: str,
    title: str | None = None,
    tags: list[str] | None = None,
    visibility: str | None = None,
    folder_id: UUID | None = None,
    idempotency_key: str | None = None,
    grant_users: list[str] | None = None,
    grant_groups: list[str] | None = None,
) -> dict:
    """업로드 파일에서 텍스트를 추출해 문서를 만들고 원본을 1판으로 보관한다 (ADR-046).

    임베딩 잡·텍스트 버전은 트리거가 만든다. 원본은 문서와 같은 트랜잭션에 들어가므로
    문서만 커밋되고 원본이 유실되는 상태가 생기지 않는다 — 호출부가 autocommit 연결을
    넘겨도 이 함수가 트랜잭션을 연다. 멱등키는 `_create_once`를 본다.

    OCR 대상(이미지, 텍스트 레이어가 빈 쪽이 있는 PDF)은 요청 안에서 인식하지 않는다. 빈 문서 텍스트와
    `extraction_status='pending'`으로 만들면 트리거가 추출 잡을 남기고, 워커가 OCR한 결과를
    `apply_extracted_text`로 반영한다 (ADR-052 결정 3·5). 이때 원본 판은 가리킬 텍스트
    버전이 아직 없어 `text_version`이 NULL이다.

    부여 대상(`grant_users`·`grant_groups`)은 문서와 같은 트랜잭션에 들어간다 (ADR-044).
    """
    visibility = _creation_scope(folder_id, visibility, grant_users, grant_groups)
    grant_users, grant_groups = _check_grantees(visibility, owner_id, grant_users, grant_groups)
    content_type = detect_content_type(filename)
    content = extract_text(data, content_type)
    extraction_status = "pending" if needs_ocr(content_type, data) else "done"
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
            folder_id=folder_id,
            empty_message=EXTRACTION_FAILED_MESSAGE,
            extraction_status=extraction_status,
            grant_users=grant_users,
            grant_groups=grant_groups,
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
            **({"folder_id": str(folder_id)} if folder_id is not None else {}),
            **_grantee_fingerprint(grant_users, grant_groups),
        ),
        insert=insert,
    )


def _check_grantees(
    visibility: str, owner_id: str | None, users: list[str] | None, groups: list[str] | None
) -> tuple[list[str], list[str]]:
    """부여 대상을 순서를 보존한 채 중복 제거하고, 효력 없는 대상(조직 공개 문서의 대상,
    소유자 자신)은 거부한다."""
    users = list(dict.fromkeys(users or []))
    groups = list(dict.fromkeys(groups or []))
    if visibility == "public" and (users or groups):
        raise GrantsOnPublicDocument
    if owner_id in users:
        raise GrantToOwner
    return users, groups


def _grantee_fingerprint(users: list[str], groups: list[str]) -> dict:
    """멱등 지문에 넣을 부여 대상. 순서·중복 차이는 같은 요청으로 본다.

    대상이 없으면 아무것도 넣지 않는다 — 이 인자가 생기기 전에 기록된 키의 지문이
    그대로 맞아야 재시도가 422가 되지 않는다.
    """
    fields: dict = {}
    if users:
        fields["grant_users"] = sorted(users)
    if groups:
        fields["grant_groups"] = sorted(groups)
    return fields


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
    되어, 상한 50MB 파일이 100MB로 OpenProxy를 지난다. 풀 연결의 기본 커서는 파라미터를
    문장에 넣으므로(#210) 이 문장만 서버 바인딩 커서로 보낸다.
    """
    async with psycopg.AsyncCursor(conn) as cur:
        await cur.execute(
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
    visibility: str | None = None,
    folder_id: UUID | None = None,
    idempotency_key: str | None = None,
    grant_users: list[str] | None = None,
    grant_groups: list[str] | None = None,
) -> dict:
    """공급자가 이미 가진 텍스트로 문서를 만든다. 원본 파일이 없으므로 filename은 NULL이다."""
    # REST는 pydantic Literal이 먼저 422로 막아 이 가드에 닿지 않는다. 그래도 두는 것은
    # MCP 서버와 스크립트가 라우터를 거치지 않고 이 함수를 직접 부르기 때문이다 — 코어가
    # 자기 계약을 스스로 지킨다.
    if content_type not in TEXT_CONTENT_TYPES:
        raise UnsupportedFileType("텍스트로 공급할 수 있는 유형은 txt, md입니다.")
    visibility = _creation_scope(folder_id, visibility, grant_users, grant_groups)
    grant_users, grant_groups = _check_grantees(visibility, owner_id, grant_users, grant_groups)

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
            folder_id=folder_id,
            empty_message="문서 텍스트는 비어 있을 수 없습니다.",
            grant_users=grant_users,
            grant_groups=grant_groups,
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
            **({"folder_id": str(folder_id)} if folder_id is not None else {}),
            **_grantee_fingerprint(grant_users, grant_groups),
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
    folder_id: UUID | None = None,
    extraction_status: str = "done",
    grant_users: list[str] | None = None,
    grant_groups: list[str] | None = None,
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
    if folder_id is not None:
        from openarchive.services.folders import ensure_folder_visible

        await ensure_folder_visible(conn, folder_id, user_id=owner_id)
    # 이름을 문서 INSERT 전에 해석한다 — 모르는 이름이면 아무것도 쓰지 않고 끝난다.
    user_ids, group_ids = await resolve_grantees(
        conn, users=grant_users or [], groups=grant_groups or []
    )

    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        INSERT INTO documents
            (title, filename, content_type, content, content_hash, owner_id, visibility, tags,
             extraction_status, folder_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            folder_id,
        ),
    )
    document = await cur.fetchone()
    await insert_grants(conn, document["id"], user_ids, group_ids)
    return document


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


DocumentSort = Literal["updated", "title"]
DOCUMENT_ORDER_BY = {
    "updated": "d.updated_at DESC, d.id",
    "title": "d.title, d.id",
}
DOCUMENT_FILTERS = f"""
{VISIBLE_TO_USER}
AND (%(status)s::text IS NULL OR d.embedding_status = %(status)s)
AND (%(extraction)s::text IS NULL OR d.extraction_status = %(extraction)s)
AND (%(tag)s::text IS NULL OR %(tag)s = ANY(d.tags))
AND (%(title)s::text IS NULL OR d.title ILIKE '%%' || %(title)s || '%%' ESCAPE '\\')
AND (%(content_type)s::text IS NULL OR d.content_type = %(content_type)s)
AND (%(folder)s::uuid IS NULL OR (d.folder_id = %(folder)s AND EXISTS (
    SELECT 1 FROM folders f WHERE f.id = %(folder)s AND {FOLDER_VISIBLE_TO_USER})))
"""


def _document_filter_params(
    user_id, embedding_status, extraction_status, tag, title_query, content_type, folder_id
) -> dict:
    title = title_query.strip() if title_query is not None else None
    if title:
        title = title.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return {
        "user": user_id,
        "status": embedding_status,
        "extraction": extraction_status,
        "tag": tag,
        "title": title or None,
        "content_type": content_type,
        "folder": folder_id,
    }


async def list_documents(
    conn: psycopg.AsyncConnection,
    *,
    user_id: str | None = None,
    embedding_status: str | None = None,
    extraction_status: ExtractionStatus | None = None,
    tag: str | None = None,
    title_query: str | None = None,
    content_type: str | None = None,
    folder_id: UUID | None = None,
    sort: DocumentSort = "updated",
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    """권한 술어와 선택적 필터를 한 쿼리로 적용한다.

    기본은 명세서의 최근 수정순(updated_at)이다.
    `embedding_status`는 컬럼 그대로다 — 인식 실패 문서는 임베딩 잡이 생기지 않아 'pending'에
    남는다. 곧 임베딩될 문서만 보려면 `extraction_status='done'`을 함께 준다 (#139, ADR-052).
    `limit`이 없으면 전부 반환한다 — 페이지는 화면이 쓰고, export·MCP는 전체를 본다 (#95-d).
    """

    if sort not in DOCUMENT_ORDER_BY:
        raise ValueError(f"허용되지 않은 문서 정렬: {sort}")
    params = _document_filter_params(
        user_id, embedding_status, extraction_status, tag, title_query, content_type, folder_id
    )
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        SELECT {SUMMARY_COLUMNS}
        FROM documents d
        WHERE {DOCUMENT_FILTERS}
        ORDER BY {DOCUMENT_ORDER_BY[sort]}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**params, "limit": limit, "offset": offset},
    )
    return await cur.fetchall()


async def count_documents(
    conn: psycopg.AsyncConnection,
    *,
    user_id: str | None = None,
    embedding_status: str | None = None,
    extraction_status: ExtractionStatus | None = None,
    tag: str | None = None,
    title_query: str | None = None,
    content_type: str | None = None,
    folder_id: UUID | None = None,
) -> int:
    """목록과 같은 열람 범위·필터로 전체 건수를 센다."""
    cur = await conn.execute(
        f"SELECT count(*) FROM documents d WHERE {DOCUMENT_FILTERS}",
        _document_filter_params(
            user_id, embedding_status, extraction_status, tag, title_query, content_type, folder_id
        ),
    )
    return (await cur.fetchone())[0]


async def list_visible_tags(
    conn: psycopg.AsyncConnection, *, user_id: str | None = None
) -> list[str]:
    """보이는 문서의 태그를 중복 없이 정렬해 반환한다."""
    cur = await conn.execute(
        f"""
        SELECT DISTINCT tag
        FROM documents d CROSS JOIN LATERAL unnest(d.tags) AS tag
        WHERE {VISIBLE_TO_USER}
        ORDER BY tag
        """,
        {"user": user_id},
    )
    return [row[0] for row in await cur.fetchall()]


async def document_progress(
    conn: psycopg.AsyncConnection, *, user_id: str | None = None
) -> dict[str, int]:
    """열람 범위 안 문서를 파이프라인 단계별로 센다. 합이 곧 보이는 문서 수다.

    인식이 끝나지 않은 문서는 임베딩 상태가 의미 없으므로 인식 단계로 센다 — 인식 실패 문서의
    embedding_status='pending'은 오지 않을 임베딩이다 (#139, ADR-052).
    """
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        SELECT count(*) FILTER (WHERE extraction_status = 'pending') AS extracting,
               count(*) FILTER (WHERE extraction_status = 'failed') AS extraction_failed,
               count(*) FILTER (WHERE extraction_status = 'done'
                                  AND embedding_status = 'pending') AS pending,
               count(*) FILTER (WHERE extraction_status = 'done'
                                  AND embedding_status = 'processing') AS processing,
               count(*) FILTER (WHERE extraction_status = 'done'
                                  AND embedding_status = 'ready') AS ready,
               count(*) FILTER (WHERE extraction_status = 'done'
                                  AND embedding_status = 'error') AS error
        FROM documents d
        WHERE {VISIBLE_TO_USER}
        """,
        {"user": user_id},
    )
    return await cur.fetchone()


async def get_document(
    conn: psycopg.AsyncConnection, document_id: UUID, *, user_id: str | None = None
) -> dict:
    """문서 상세와 텍스트 버전 이력을 반환한다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        SELECT {SUMMARY_COLUMNS}, content, d.folder_id,
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
    folder_id = document.pop("folder_id")
    document["folder"], _ = await _folder_info(conn, folder_id, user_id)
    # 소유자에게만 「볼 수 없는 폴더 안」임을 알린다 — 남에게는 폴더의 존재도 새지 않는다 (D5).
    document["hidden_folder"] = (
        folder_id is not None and document["folder"] is None and document["owner_id"] == user_id
    )

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


async def find_same_original(
    conn: psycopg.AsyncConnection, *, owner_id: str, data: bytes
) -> UUID | None:
    """이 바이트와 같은 원본 판을 가진 소유자의 문서. import 재실행이 두 벌을 만들지 않게 한다.

    교체된 옛 판도 본다 — 판은 지워지지 않으므로(ADR-046) 옛 파일을 다시 넣는 것도 이미 있는 것이다.
    """
    cur = await conn.execute(
        f"""
        SELECT f.document_id
        FROM document_files f
        JOIN documents d ON d.id = f.document_id
        WHERE d.owner_id = %s AND f.sha256 = %s AND {NOT_TRASHED}
        LIMIT 1
        """,
        (owner_id, hashlib.sha256(data).hexdigest()),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def find_same_text(
    conn: psycopg.AsyncConnection, *, owner_id: str, content: str
) -> UUID | None:
    """이 텍스트를 가진 소유자의 문서. 원본이 있는 문서도 본다 — export는 파일 문서도 텍스트로
    내보내므로, 같은 설치에 다시 넣을 때 두 벌을 만들지 않게. 인식 전 문서는 텍스트가 비어 있을 뿐이다."""
    cur = await conn.execute(
        f"""
        SELECT d.id FROM documents d
        WHERE d.owner_id = %s AND {NOT_TRASHED} AND extraction_status = 'done' AND content_hash = %s
        LIMIT 1
        """,
        (owner_id, hashlib.sha256(content.encode("utf-8")).hexdigest()),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def get_original_file(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str | None,
    file_version: int | None = None,
    preview: bool = False,
) -> dict:
    """원본 한 판의 바이트를 돌려준다. `file_version`이 없으면 최신 판이다.

    열람 검증을 먼저 한다 — 볼 수 없는 문서는 원본 유무와 관계없이 DocumentNotFound라
    원본의 존재가 문서의 존재를 누출하지 않는다 (ADR-027).
    """
    await ensure_visible(conn, document_id, user_id=user_id)
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT filename, data, sha256, file_version
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
    if (
        preview
        and PurePath(original["filename"]).suffix.lower().lstrip(".") not in PREVIEWABLE_EXTENSIONS
    ):
        raise OriginalNotPreviewable
    await conn.execute(
        "SELECT record_original_preview(%s, %s)"
        if preview
        else "SELECT record_original_download(%s, %s)",
        (document_id, original.pop("file_version")),
    )
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
    ocr = needs_ocr(content_type, data)
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
                # pending으로 바뀌면 022 트리거가 추출 잡을 만든다. 모든 쪽에 텍스트 레이어가 있는 원본으로
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

    최신 원본이 OCR 대상(이미지, 텍스트 레이어가 빈 쪽이 있는 PDF)이면 텍스트를 쓰지 않고 추출 중으로
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
    if needs_ocr(content_type, original[1]):
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
    chunk: int | None = None,
) -> dict:
    """과거 텍스트 버전의 본문을 돌려준다 (ADR-037 결정 1).

    열람 범위는 문서 본체와 같은 술어를 쓴다. 볼 수 없는 문서의 버전은 없는 것과 같이
    다뤄야 한다 — 버전 번호의 존재만 알려줘도 문서의 존재와 수정 횟수가 새어 나간다.

    `chunk`를 주면 그 텍스트 버전을 워커와 같은 청킹으로 잘라 그 번호 대목의 위치를
    `passage_start`·`passage_end`로 싣는다 — 답변 인용이 「그 버전의 그 자리」로 가는 길이다
    (ADR-043, #96 b). 브라우저가 쓰는 UTF-16 단위로 센다. 번호에 맞는 청크가 없으면 싣지 않는다.
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
    if chunk is not None:
        content = document_version["content"]
        spans = chunk_spans(content)
        if chunk < len(spans):
            start, end = spans[chunk]
            document_version["passage_start"] = _utf16_len(content[:start])
            document_version["passage_end"] = _utf16_len(content[:end])
    return document_version


async def diff_versions(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str,
    base: int,
    target: int,
) -> dict:
    """열람 가능한 문서의 두 텍스트 버전을 한 번에 읽어 비교한다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        f"""
        SELECT v.version, v.content
        FROM document_versions v
        JOIN documents d ON d.id = v.document_id
        WHERE v.document_id = %(id)s
          AND v.version IN (%(base)s, %(target)s)
          AND {VISIBLE_TO_USER}
        """,
        {"id": document_id, "base": base, "target": target, "user": user_id},
    )
    contents = {row["version"]: row["content"] for row in await cur.fetchall()}
    if base not in contents or target not in contents:
        raise DocumentNotFound
    old, new = contents[base], contents[target]
    identical = old == new
    return {
        "base": base,
        "target": target,
        "identical": identical,
        "hunks": [] if identical else _diff_lines(old, new),
    }


def _diff_lines(old: str, new: str) -> list[dict]:
    old_lines, new_lines = old.splitlines(), new.splitlines()
    hunks = []
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for group in matcher.get_grouped_opcodes(3):
        lines = []
        for op, a_start, a_end, b_start, b_end in group:
            if op == "equal":
                lines.extend({"op": "equal", "text": text} for text in old_lines[a_start:a_end])
            if op in ("delete", "replace"):
                lines.extend({"op": "removed", "text": text} for text in old_lines[a_start:a_end])
            if op in ("insert", "replace"):
                lines.extend({"op": "added", "text": text} for text in new_lines[b_start:b_end])
        hunks.append({"lines": lines})
    return hunks


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


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


async def _load_owner_document(
    conn: psycopg.AsyncConnection, document_id: UUID, user_id: str | None
) -> None:
    """열람 범위 설정은 소유자만 본다 (ADR-044 관리 경로 결정 5).

    익명은 소유자일 수 없으므로 공개 문서라도 존재를 알리지 않는다.
    """
    if user_id is None:
        raise DocumentNotFound
    await _load_for_write(conn, document_id, user_id)


async def _folder_info(conn, folder_id, user_id):
    from openarchive.services.folders import (
        FolderNotFound,
        ensure_folder_visible,
        folder_path,
    )
    from openarchive.services.folders import (
        _read_access as read_folder_access,
    )

    if folder_id is None:
        return None, None
    try:
        folder = await ensure_folder_visible(conn, folder_id, user_id=user_id)
    except FolderNotFound:
        return None, None
    path = await folder_path(conn, folder_id)
    scope = await read_folder_access(conn, path[0]["id"])
    return {"id": folder_id, "name": folder["name"], "path": path}, scope


async def move_document(
    conn: psycopg.AsyncConnection, document_id: UUID, *, user_id: str, folder_id: UUID | None
) -> dict:
    """소유자의 문서를 옮기되 개별 범위와 상속 여부는 보존한다."""
    from openarchive.services.folders import ensure_folder_visible

    async with conn.transaction():
        cur = await conn.execute(
            "SELECT 1 FROM documents WHERE id=%s FOR NO KEY UPDATE", (document_id,)
        )
        if await cur.fetchone() is None:
            raise DocumentNotFound
        await _load_owner_document(conn, document_id, user_id)
        if folder_id is not None:
            await ensure_folder_visible(conn, folder_id, user_id=user_id)
        await conn.execute(
            "UPDATE documents SET folder_id=%s, updated_at=now() WHERE id=%s",
            (folder_id, document_id),
        )
        return await get_document(conn, document_id, user_id=user_id)


async def _read_access(conn: psycopg.AsyncConnection, document_id: UUID, user_id: str | None) -> dict:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT d.visibility, d.follows_folder, d.folder_id,
               COALESCE((SELECT array_agg(u.username ORDER BY u.username)
                         FROM document_grants g JOIN users u ON u.id = g.user_id
                         WHERE g.document_id = d.id), ARRAY[]::text[]) AS users,
               COALESCE((SELECT array_agg(gr.name ORDER BY gr.name)
                         FROM document_grants g JOIN groups gr ON gr.id = g.group_id
                         WHERE g.document_id = d.id), ARRAY[]::text[]) AS groups
        FROM documents d WHERE d.id = %s
        """,
        (document_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise DocumentNotFound
    folder_id = row.pop("folder_id")
    row["folder"], row["folder_scope"] = await _folder_info(conn, folder_id, user_id)
    # 소유자 전용 응답이다 — 폴더 이름·경로 없이 「볼 수 없는 폴더 안」이라는 사실만 싣는다.
    row["hidden_folder"] = folder_id is not None and row["folder"] is None
    return row


async def get_access(
    conn: psycopg.AsyncConnection, document_id: UUID, *, user_id: str | None
) -> dict:
    """열람 범위 설정(공개범위 + 부여 대상 이름)을 소유자에게만 돌려준다.

    목록·검색 응답에 싣지 않는 이유는 비소유자에게 "누가 이 문서를 보는가"가 새기 때문이다.
    """
    await _load_owner_document(conn, document_id, user_id)
    return await _read_access(conn, document_id, user_id)


async def set_access(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str | None,
    visibility: str | None = None,
    users: list[str] | None = None,
    groups: list[str] | None = None,
    follows_folder: bool | None = None,
) -> dict:
    """열람 범위를 통째로 교체한다. 실패하면 아무것도 바뀌지 않는다.

    부여는 차이만 반영 — 감사 로그가 실제 변경만 남기게 한다(ADR-055).

    공개범위만 바꾸므로 `UPDATE OF content_hash` 트리거는 발화하지 않는다 — 버전도
    재임베딩도 없다. 열람 범위는 조회 시점 술어라 그래프·관계도 다시 만들 필요가 없다(ADR-027).
    """
    # 함께 온 범위를 버리면 좁혔다고 믿은 문서가 폴더 범위를 따르게 된다.
    if follows_folder is True and (visibility is not None or users or groups):
        raise ValueError("폴더 범위를 따르면 공개범위·부여 대상을 함께 지정할 수 없습니다.")
    if follows_folder is not True and visibility not in VISIBILITY_VALUES:
        raise InvalidVisibility("공개범위는 public, private 중 하나여야 합니다.")
    if follows_folder is not True:
        users, groups = _check_grantees(visibility, user_id, users, groups)
    async with conn.transaction():
        await _load_owner_document(conn, document_id, user_id)
        # 현재 부여 조회와 차이 반영 사이에 동시 교체가 끼지 않게 한다. 워커·024와 같은
        # 수준으로 잠가 FK 확인(FOR KEY SHARE)과는 부딪히지 않게 한다.
        cur = await conn.execute(
            "SELECT folder_id, follows_folder FROM documents WHERE id = %s FOR NO KEY UPDATE",
            (document_id,),
        )
        locked = await cur.fetchone()
        if locked is None:
            raise DocumentNotFound
        # 전환 없이 범위만 받으면 visibility 컬럼만 바뀌고 실효 범위는 폴더 그대로다 — 저장은
        # 성공인데 아무것도 열리거나 닫히지 않는다. follows_folder 기본값이 true라 folder_id와 함께 본다.
        if follows_folder is None and locked[0] is not None and locked[1]:
            raise ValueError(
                "폴더 범위를 따르는 문서는 개별 지정으로 바꿔야 공개범위·부여 대상을 정할 수 있습니다."
            )
        if follows_folder is not None:
            if locked[0] is None:
                raise ValueError("폴더에 없는 문서는 폴더 범위를 따를 수 없습니다.")
            await conn.execute(
                "UPDATE documents SET follows_folder=%s, updated_at=now() WHERE id=%s",
                (follows_folder, document_id),
            )
        if follows_folder is True:
            return await _read_access(conn, document_id, user_id)
        # 이름을 변경 전에 해석한다 — 모르는 이름이면 아무것도 바뀌지 않는다.
        user_ids, group_ids = await resolve_grantees(conn, users=users, groups=groups)
        cur = await conn.execute(
            "SELECT user_id, group_id FROM document_grants "
            "WHERE document_id = %s AND share_id IS NULL",
            (document_id,),
        )
        current = await cur.fetchall()
        current_users = {row[0] for row in current if row[0] is not None}
        current_groups = {row[1] for row in current if row[1] is not None}
        await conn.execute(
            "UPDATE documents SET visibility = %s, updated_at = now() WHERE id = %s",
            (visibility, document_id),
        )
        # 공유 부여는 남긴다 — 공유는 열람 범위와 별개 축이라 공유 화면에서만 바뀐다
        # (ADR-044 「공유」 결정 2). 지우면 열람 범위를 고칠 때마다 외부 공유가 조용히 끊긴다.
        await conn.execute(
            "DELETE FROM document_grants WHERE document_id = %s AND share_id IS NULL "
            "AND (user_id = ANY(%s) OR group_id = ANY(%s))",
            (document_id, list(current_users - set(user_ids)), list(current_groups - set(group_ids))),
        )
        await insert_grants(
            conn, document_id,
            [user for user in user_ids if user not in current_users],
            [group for group in group_ids if group not in current_groups],
        )
        return await _read_access(conn, document_id, user_id)


async def _current_version(conn: psycopg.AsyncConnection, document_id: UUID) -> int:
    row = await (
        await conn.execute("SELECT version FROM documents WHERE id = %s", (document_id,))
    ).fetchone()
    if row is None:
        raise DocumentNotFound
    return row[0]


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
