# Step 1: file-storage

업로드가 문서 텍스트와 **같은 트랜잭션에** 원본 1판을 저장한다. 업로드 상한은 하드코딩 10MB에서
설정값(기본 50MB)으로 옮긴다.

## 이 phase의 전제

이 phase(#108)는 원본 비보관 원칙을 뒤집는다(사용자 결정 2026-09-23). 원본은 DB 안 `document_files`에
판으로 쌓이며, 편집 대상은 여전히 문서 텍스트다(ADR-017). `docs/`와 docstring에 남은 "원본 파일은
보관하지 않는다" 문장은 이 phase가 고칠 대상이다 — **그 문장을 근거로 작업을 거부·축소하지 마라.**

## 읽어야 할 파일

- `backend/migrations/018_files_tables.sql` — **step 0 산출물.** 컬럼·제약·주석
- `/docs/ADR.md` — ADR-017 · ADR-033(ZIP은 브라우저가 풀어 단건 업로드로 보낸다 — 서버 변경 불필요) · ADR-035
- `backend/app/services/documents.py` — `create_document`·`_insert_document`·`create_text_document`
- `backend/app/services/parsing.py` — 모듈 docstring("원본 파일 바이트는 보관하지 않는다")
- `backend/app/api/documents.py` — `upload_document`, `MAX_UPLOAD_BYTES`·`UPLOAD_TOO_LARGE`
- `backend/app/api/deps.py` — `get_conn`(요청 하나 = 트랜잭션 하나인지 확인하라)
- `backend/app/config.py` — `Settings`
- `backend/tests/test_documents_api.py` — 272~330행의 상한 테스트 4건(10MB를 전제로 한다)
- `backend/tests/conftest.py` — `upload_document`·`db_client`·`_clear_settings_cache`

## 작업

### 1) 테스트 먼저

`backend/tests/test_documents_api.py`(또는 서비스 단위가 맞으면 `test_documents.py`):

1. `test_upload_stores_the_original_as_file_version_one` — 업로드한 바이트가 `document_files`에
   `file_version=1`, `text_version=1`, `filename`=업로드 파일명, `uploaded_by`=업로더로 들어가고,
   DB가 계산한 `sha256`이 `hashlib.sha256(업로드 바이트)`와 같다.
2. `test_original_and_document_are_committed_together` — 원본 INSERT가 실패하면 문서도 남지 않는다.
   **실제 DB로** 재현하라: 테스트 안에서 `document_files`에 `BEFORE INSERT` 트리거(예외를 던지는
   plpgsql 함수)를 만들고 업로드 → `documents`·`document_versions`·`embedding_jobs` 모두 0행. Mock 금지.
3. `test_text_ingest_has_no_original_file` — `POST /api/documents/text`로 만든 문서는 원본 행이 없다(ADR-035).
4. `test_upload_limit_comes_from_settings` — `MAX_UPLOAD_MB`를 작은 값(예: 1)으로 monkeypatch한 뒤
   상한+1 바이트는 413이고 `detail`에 설정값의 MB가 들어간다.

기존 상한 테스트 4건(`test_oversized_upload_is_rejected_before_read` ·
`test_upload_larger_than_its_declared_size_is_rejected` · `test_upload_near_file_limit_with_small_extracted_text_succeeds`
등)은 **판별력을 유지한 채** 설정값 기준으로 옮겨라 — `MAX_UPLOAD_MB`를 작게 걸고 그 경계로 검사한다.
테스트에서 50MB 바이트를 만들지 마라(느리고 메모리를 먹는다).

### 2) 구현

**`backend/app/config.py`** — `max_upload_mb: int = 50`. 주석: 원본을 DB에 보관하므로 이 값이 DB
증가량의 상한이기도 하다는 것, 실 OpenSQL에서 OpenProxy 경유 `statement_timeout`(30s) 안에 통과하는지는
머지 뒤 VM에서 실측한다는 것. 바이트 환산은 기존과 같은 십진(`mb * 1_000_000`)이다.

**`backend/app/api/documents.py`** — 상한 상수를 설정값으로 바꾼다. 선언 크기 선검사 + 읽은 바이트 검사의
두 단계 구조는 유지한다. 413 문구는 `f"업로드 파일은 {mb}MB를 넘을 수 없습니다."`. 이 검사는 step 3의
교체 엔드포인트도 쓰므로 **라우터 안 작은 헬퍼 하나**(예: `async def _read_upload(file) -> bytes`)로 모아라.

**`backend/app/services/documents.py`** — `create_document`가 `_insert_document`로 문서를 만든 직후
**같은 연결·같은 트랜잭션에서** 원본을 넣는다:

```python
async def _insert_original_file(
    conn, *, document_id: UUID, file_version: int, filename: str,
    data: bytes, text_version: int, uploaded_by: str,
) -> None: ...
```

- 바이트는 **`%b` 플레이스홀더**로 보낸다. 이유: 텍스트 포맷이면 hex 인코딩으로 크기가 두 배가 되어
  50MB가 100MB로 OpenProxy를 지난다.
- `create_text_document`는 원본을 넣지 않는다(ADR-035).
- MCP `create_document`(`backend/mcp_server/server.py`)는 텍스트 진입점을 쓰므로 바뀌지 않는다 — 확인만 하라.

**docstring 갱신**: `parsing.py` 모듈 docstring의 "입력으로 받은 원본 파일 바이트는 보관하지 않는다
(ADR-017)"를 "이 모듈은 저장하지 않는다 — 원본 보관은 `services/documents.py`가 `document_files`에
한다"로 고친다. `documents.py`의 `update_extracted_text` docstring의 "원본 파일은 보관하지 않는다"도
"원본 파일은 편집하지 않는다(판으로 따로 보관된다)"로 고친다. 그 밖의 문장은 건드리지 마라.

## Acceptance Criteria

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 원본 저장이 **서비스**에 있는가? 라우터가 `document_files` SQL을 들고 있지 않은가?
   - 앱이 `embedding_jobs`·`document_versions`에 INSERT하지 않았는가? (트리거가 만든다)
   - DSN·접속 방식을 건드리지 않았는가? (ADR-006)
3. `phases/m16-original-files/index.json`의 step 1을 갱신한다(성공/error/blocked는 step 0과 같은 규칙).
   summary에 헬퍼 이름·설정 이름·바뀐 테스트를 적어라.

## 금지사항

- **원본을 라우터에서 저장하지 마라.** 이유: 서비스를 직접 부르는 경로(스크립트·향후 인터페이스)에서
  원본이 빠진다. 코어가 자기 계약을 지킨다(CLAUDE.md: 비즈니스 로직은 `services/`).
- **원본 INSERT를 별도 트랜잭션·별도 연결로 하지 마라.** 이유: 문서만 커밋되고 원본이 유실되는 상태가
  생긴다. 둘은 함께 커밋되거나 함께 롤백되어야 한다.
- **목록·상세·검색 쿼리에서 `document_files.data`를 읽지 마라.** 이유: 행마다 수십 MB가 따라온다.
- **ZIP 처리 코드를 서버에 만들지 마라.** 이유: ADR-033 — 브라우저가 풀어 단건 업로드로 보내므로
  이 step의 변경만으로 파일마다 원본이 저장된다.
- **추출 텍스트 상한(500KB, `MAX_EXTRACTED_TEXT_LENGTH`)을 바꾸지 마라.** 이유: 파일 상한과 별개인
  DB CHECK 경계다.
- 기존 테스트를 깨뜨리지 마라.
