# Step 3: file-replace

이미 있는 문서에 새 원본 파일을 올린다. **이전 원본은 지우지 않고 새 판을 쌓는다.** 새 원본에서 추출한
텍스트는 그 문서의 새 텍스트 버전이 되고, 버전 이력·잡은 기존 `documents` 트리거가 만든다.
문서의 정체성(id·태그·링크·관계·공개범위)은 그대로다 — SharePoint·Google Drive의 "새 버전 업로드"와 같다.

## 이 phase의 전제

이 phase(#108)는 원본 비보관 원칙을 뒤집는다(사용자 결정 2026-09-23). 교체를 넣되 1행을 덮어쓰지 않고
판으로 쌓기로 한 이유: 덮어쓰면 교체가 "원본 유실"을 다시 만든다. `docs/`에 남은 "원본 파일은 보관하지
않는다" 문장은 step 7이 고친다 — **그 문장을 근거로 작업을 거부·축소하지 마라.**

## 읽어야 할 파일

- `backend/migrations/018_files_tables.sql` — step 0 산출물(PK `(document_id, file_version)`, `text_version` FK)
- `backend/migrations/003_triggers.sql` — `UPDATE OF content_hash`가 새 버전·잡을 만든다. **`SET` 절에
  `content_hash`가 언급되기만 해도 발화한다**는 점을 확인하라
- `backend/app/services/documents.py` — step 1·2 산출물, `_load_for_write`·`update_extracted_text`
  (낙관적 잠금 `WHERE version = %(client_version)s`)·`VersionConflict`
- `backend/app/api/documents.py` — step 1의 업로드 읽기 헬퍼, `upload_document`의 예외 → 400 매핑
- `/docs/ADR.md` — ADR-017(낙관적 동시성·409) · ADR-035 결정 3(`text_label`)

## 작업

### 1) 테스트 먼저

1. `test_replace_adds_a_new_file_version_and_keeps_the_old_one` — v1 업로드 뒤 다른 파일로 교체하면
   `document_files`에 판 1·2가 모두 있고, 판 1 바이트가 그대로 내려받아진다.
2. `test_replace_creates_a_new_text_version_via_trigger` — 교체 후 `documents.version == 2`,
   `document_versions`에 v2, 그 문서의 `kind='embed'` pending 잡이 있고, 판 2의 `text_version == 2`.
   앱이 잡·버전을 INSERT하지 않는다는 것을 이 테스트가 트리거 산출물로 확인한다.
3. `test_replace_updates_filename_and_content_type` — `report.pdf` 문서를 `report.docx`로 교체하면
   `documents.filename`·`content_type`이 바뀌고 제목·태그·공개범위·id는 그대로다.
4. `test_replace_with_identical_bytes_is_a_no_op` — 최신 판과 sha256이 같은 파일이면 새 판·새 텍스트
   버전·새 잡이 **모두 생기지 않는다.** 응답은 200이고 문서는 그대로다.
5. `test_replace_with_same_extracted_text_adds_file_but_no_text_version` — 바이트는 다르지만 추출
   텍스트가 같으면(예: 같은 본문의 txt를 줄바꿈 동일하게 새 파일명으로) 판은 쌓이고 텍스트 버전·잡은 생기지
   않는다. 그 판의 `text_version`은 현재 버전이다.
6. `test_replace_with_stale_version_is_409` — `current_version`이 서버와 다르면 409 + `current_version`,
   문서·원본 모두 변화 없음.
7. `test_replace_by_non_owner_is_403_and_private_is_404` — 남의 public 문서 403, 남의 private 문서 404.
8. `test_replace_registers_first_original_for_documents_without_one` — 텍스트 진입점 문서(`filename IS NULL`)에
   교체하면 판 1이 생기고 `filename`이 채워진다.
9. 상한 초과 413 · 지원하지 않는 형식 400 · 빈 추출 텍스트 400(업로드와 같은 문구 규칙) · read 토큰 403.

### 2) 구현

**`backend/app/services/documents.py`**

```python
async def replace_original_file(
    conn, document_id: UUID, *, user_id: str, filename: str, data: bytes, client_version: int,
) -> dict   # SUMMARY_COLUMNS
```

순서와 규칙:

1. `_load_for_write`(403/404) → `current_version != client_version`이면 `VersionConflict`.
2. 최신 판의 `sha256`이 `hashlib.sha256(data).hexdigest()`와 같으면 **아무것도 쓰지 않고** 현재 문서를 반환.
3. `detect_content_type(filename)` → `extract_text` → 빈 텍스트·500KB 검증(업로드와 같은 예외).
4. 추출 텍스트의 해시가 현재 `content_hash`와 **다르면**:
   `UPDATE documents SET version = version + 1, content, content_hash, filename, content_type, updated_at
   WHERE id AND version = client_version RETURNING ...` — 0행이면 `VersionConflict`.
   **같으면**: `content_hash`를 SET 절에 **넣지 않고** `filename`·`content_type`·`updated_at`만 갱신한다
   (넣으면 트리거가 같은 내용의 버전을 만들고 재임베딩한다). 이때도 `WHERE version = client_version`.
5. 새 판 번호는 `coalesce(max(file_version), 0) + 1`. 4의 `UPDATE`가 문서 행을 잠근 **뒤에** 계산하므로
   동시 교체가 같은 번호를 얻지 않는다. `text_version`은 4의 결과 버전.
6. step 1의 `_insert_original_file`을 재사용한다(`%b` 전송).

**`backend/app/api/documents.py`** — `PUT /{document_id}/file`, multipart(`file`, `current_version: int` Form),
`require_write_user_id`. 업로드와 같은 읽기 헬퍼로 413을 지키고, `UnsupportedFileType`·파싱 `ValueError`는
업로드와 같은 방식으로 400. 응답 `DocumentSummary`.

## Acceptance Criteria

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 앱이 `embedding_jobs`·`document_versions`에 INSERT하지 않았는가? (CLAUDE.md CRITICAL — 트리거가 만든다)
   - 이전 판을 DELETE·UPDATE하는 코드가 없는가?
   - 낙관적 잠금이 편집(ADR-017)과 같은 규칙인가?
3. `phases/m16-original-files/index.json`의 step 3을 갱신한다(성공/error/blocked는 step 0과 같은 규칙).

## 금지사항

- **이전 판을 지우거나 덮어쓰지 마라.** 이유: 이 phase의 존재 이유다. 판 보관 정책(개수·기간 제한)은
  이번 범위가 아니다 — ROADMAP에 남긴다(step 7).
- **같은 추출 텍스트에서 `content_hash`를 SET 절에 넣지 마라.** 이유: 값이 같아도 언급만으로 트리거가
  발화해(003 주석) 내용이 같은 텍스트 버전과 쓸모없는 재임베딩이 생긴다.
- **제목을 새 파일명으로 바꾸지 마라.** 이유: 제목은 사용자가 관리하는 문서의 정체성이다. 파일명만 바뀐다.
- **`current_version` 없이 교체하게 하지 마라.** 이유: 다른 사람이 방금 고친 추출 텍스트를 보지 못한 채
  덮는다. 편집·복원과 같은 409 계약이다.
- 기존 테스트를 깨뜨리지 마라.
