# Step 3: extract-upload

이 phase(#135)는 이미지·스캔 PDF를 워커 잡으로 OCR한다(**ADR-052** — 먼저 읽어라). 이 step은 서비스 계층의
**생성과 반영**만 다룬다: OCR 대상 업로드를 「추출 중」 문서로 만들기, 워커가 부를 반영 함수, 응답에 추출 상태
싣기, 진단에서 추출 미완료 문서 빼기. 편집 차단·재추출·교체는 step 4, 워커는 step 5다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-052** 전체, ADR-017·ADR-035(용어), ADR-046(원본 판), ADR-047(멱등키)
- `phases/m19-ocr/index.json` — step 1·2 summary
- `backend/app/services/parsing.py` — step 1의 `needs_ocr`·`IMAGE_CONTENT_TYPES`
- `backend/migrations/021_extract_tables.sql`·`022_extract_triggers.sql` — step 2 산출물. **트리거 WHEN 조건을 정확히 이해하라**
- `backend/app/services/documents.py` — `create_document`·`_insert_document`·`_insert_original_file`·`_write_text`·`SUMMARY_COLUMNS`
- `backend/app/api/schemas.py`·`backend/app/api/documents.py`·`backend/app/main.py`
- `backend/app/services/diagnostics.py` — `ORPHANS_SQL`·`DUPLICATES_SQL`(identical은 `content_hash` 일치)
- `backend/tests/test_documents.py`·`test_documents_api.py`·`test_diagnostics.py`

## 작업

### 1) 테스트 먼저

`test_documents.py` / `test_documents_api.py` (실 DB, 픽스처 `scan_tax_page1.jpg`·`scan_tax_pages.pdf`):
1. jpg 업로드 → 201, `extraction_status == "pending"`, 문서 텍스트 `""`, `document_versions` 0행,
   `document_files` 1판(`text_version IS NULL`), `kind='extract'` pending 잡 1개(트리거가 만든 것), `kind='embed'` 잡 0개.
2. 스캔 PDF 업로드 → 같은 결과. **텍스트 레이어가 있는 PDF·DOCX 등은 지금과 같다**(`done`, v1, embed 잡).
3. 빈 추출 HWP·TXT 등 OCR 대상이 아닌 형식은 여전히 400. 메시지는 `"문서에서 텍스트를 추출하지 못했습니다."`로 바뀐다 —
   "스캔 이미지 PDF는 지원하지 않습니다"는 이제 거짓이다. 이 문구를 단언하는 기존 테스트 4곳(`test_documents_api.py`에서
   grep)은 새 문구로 고친다. 명세 변경이며 검증을 약화하는 것이 아니다 — 상태 코드·무저장 단언은 그대로 둔다.
4. 같은 `Idempotency-Key`로 jpg를 다시 보내면 같은 `pending` 문서가 재생된다(새 문서·새 잡 없음).
5. `apply_extracted_text` (아래 시그니처):
   - 첫 추출(본문 빈 `pending`) → `"applied"`, 버전은 1 그대로, v1이 그 텍스트로 기록, embed 잡 생성,
     `document_files.text_version`이 1로 채워진다, `extraction_status == "done"`.
   - 본문 있는 `pending`(재추출 중) + 다른 텍스트 → `"applied"`, 버전 +1, 새 텍스트 버전, embed 잡.
   - 본문 있는 `pending` + 같은 텍스트 → `"unchanged"`, 버전·텍스트 버전·잡 증가 없음, `done`.
   - 빈 결과(공백뿐)·500KB 초과 → `"failed"`, 본문 그대로, `extraction_status == "failed"`, 텍스트 버전·embed 잡 없음.
   - `pending`이 아닌 문서·없는 문서 → `"skipped"`, 아무것도 쓰지 않는다.
6. `test_diagnostics.py`: 추출 중인 문서 둘(둘 다 빈 텍스트 → `content_hash` 같음)이 `identical` 중복으로 잡히지 않고,
   고아 목록에도 나오지 않는다. `failed` 문서도 같다.
7. 문서 목록·상세 응답에 `extraction_status`가 있다.

### 2) 구현

```python
# backend/app/services/documents.py
ExtractionOutcome = Literal["applied", "unchanged", "failed", "skipped"]

async def apply_extracted_text(
    conn: psycopg.AsyncConnection, document_id: UUID, content: str
) -> ExtractionOutcome: ...
```

- `SUMMARY_COLUMNS`에 `extraction_status`. `DocumentSummary` 스키마에 `extraction_status: Literal["pending","failed","done"]`.
- `create_document`: `extract_text` 결과에 `needs_ocr(content_type, content)`가 참이면 빈 문서 텍스트와
  `extraction_status='pending'`으로 INSERT하고(빈 추출 거부를 건너뛴다), 원본 판의 `text_version`은 NULL로 넣는다.
  그 밖에는 지금과 같다. 멱등(`_create_once`)·같은 트랜잭션 보관은 유지한다.
- `apply_extracted_text` 핵심 규칙:
  - 자기 트랜잭션(호출자가 이미 열었으면 savepoint)에서 문서 행을 `FOR UPDATE`로 잠그고 판정한다. 워커는 이것을
    자기 잡 소유 확인(`lock_owned_job`)과 같은 트랜잭션 안에서 부른다(step 5).
  - **본문·`content_hash`·`extraction_status='done'`을 반드시 한 UPDATE 문장에서 쓴다.** 003 트리거는
    `UPDATE OF content_hash`에서 `NEW.extraction_status = 'done'`일 때만 발화하므로, 나눠 쓰면 텍스트 버전·임베딩
    잡이 조용히 생기지 않는다.
  - 첫 추출(현재 본문이 비어 있음)은 버전을 올리지 않는다 — v1이 첫 텍스트다. 같은 트랜잭션에서
    `document_files`의 `text_version IS NULL` 행을 그 버전으로 채운다.
  - 재추출 중(본문 있음)은 편집 경로와 같이 버전을 올린다. `content_hash`가 같으면 `extraction_status`만 `done`으로
    바꾸고 `content_hash`는 SET 절에 **언급하지 않는다**(언급만 해도 트리거가 발화한다 — `reextract_text` docstring 참조).
  - 빈 결과·`MAX_EXTRACTED_TEXT_LENGTH` 초과는 `failed`로 표시만 한다. 예외를 던지지 않는다 — 워커가 재시도하지 않게.
- `diagnostics.py`: 고아·`identical` 중복의 후보 문서를 `extraction_status = 'done'`으로 한정한다. 열람 범위 조건은 그대로.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_documents.py tests/test_documents_api.py tests/test_diagnostics.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: 앱 코드가 `embedding_jobs`·`document_versions`에 INSERT하지 않는가(CLAUDE.md CRITICAL)? 비즈니스 로직이
   `services/`에 있고 라우터는 재사용만 하는가? 원본 파일 없는 문서에 "추출"이라는 말을 새로 쓰지 않았는가(ADR-035)?
3. `phases/m19-ocr/index.json`의 step 3을 갱신한다. summary에 `apply_extracted_text` 시그니처와 반환값 의미를 적는다.

## 금지사항

- 업로드 요청 안에서 `ocr_text`를 부르지 마라. 이유: ADR-052 결정 3 — 추출은 워커 잡이다.
- `embedding_jobs`에 직접 INSERT해 추출 잡을 만들지 마라. 이유: CLAUDE.md CRITICAL — `extraction_status='pending'`이 트리거를 발화한다.
- 편집·복원·재추출·교체 경로를 고치지 마라(문구 교체 제외). 이유: step 4의 범위다.
- 워커(`app/worker.py`)를 고치지 마라. 이유: step 5의 범위다. 그 사이 워커는 `extract` 잡을 `embed`처럼 다룬다 —
  이 step의 테스트에서 워커(`process_once`·`drain`)를 돌려 추출 잡을 처리하려 하지 마라.
- 기존 테스트를 깨뜨리지 마라
