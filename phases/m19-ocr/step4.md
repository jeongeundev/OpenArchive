# Step 4: extract-guards

이 phase(#135)는 이미지·스캔 PDF를 워커 잡으로 OCR한다(**ADR-052** — 먼저 읽어라, 특히 결정 6·7·8). step 3이 새
문서를 「추출 중」으로 만들었다. 이 step은 **이미 있는 문서의 텍스트를 바꾸는 경로**를 추출 상태에 맞춘다:
편집·복원·재추출·원본 교체, 그리고 `/admin/status`·CLI 재추출.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-052**, ADR-037(복원은 새 버전), ADR-046(교체는 판 쌓기)
- `phases/m19-ocr/index.json` — step 1~3 summary
- `backend/app/services/documents.py` — `update_extracted_text`·`_write_text`·`restore_version`·`reextract_text`·
  `replace_original_file`·`apply_extracted_text`(step 3)
- `backend/app/services/parsing.py` — `needs_ocr`
- `backend/app/services/system.py` — `get_system_status`·`reextract_one`·`reextract_all`
- `backend/app/main.py`(예외 → 응답 매핑) · `backend/app/api/schemas.py` · `backend/app/cli.py`(`run_reextract` 출력)
- `backend/tests/test_documents_api.py`·`test_system.py`·`test_system_api.py`·`test_cli.py`

## 작업

### 1) 테스트 먼저

1. `extraction_status='pending'`인 문서에 편집(`PUT /api/documents/{id}`)·복원·재추출·원본 교체 → **409**, 문서·버전·원본 판 불변.
   태그 수정·삭제는 막히지 않는다.
2. 빈 본문 `failed` 문서(새 스캔 문서의 인식 실패)에 편집·복원 → 409(편집할 텍스트가 없다 — 교체나 재추출로 벗어난다).
3. 본문 있는 `failed` 문서(재추출 실패)를 편집하면 새 버전이 생기고 `extraction_status`가 `done`이 된다(같은 UPDATE).
4. 재추출: 최신 원본이 이미지거나 텍스트 레이어 없는 PDF → 200, 이전 텍스트·버전 그대로, `extraction_status == "pending"`,
   extract 잡 1개(트리거), 응답 `changed == false`. 버전 검사(`current_version`)는 지금처럼 먼저 적용된다.
   빈 본문 `failed` 문서도 재추출로 다시 `pending`이 된다(재시도 경로).
5. 원본 교체: 새 원본이 OCR 대상 → 200, 새 판이 쌓이고(`text_version`은 현재 버전, 현재 본문이 비었으면 NULL),
   `filename`·`content_type`이 새 원본으로 바뀌고, 텍스트는 그대로, `extraction_status == "pending"`, extract 잡.
   OCR 대상이 아닌 원본으로 교체하면 지금과 같고, 교체 전이 `failed`였다면 `done`이 된다.
6. `/admin/status`에 `extraction_pending`·`extraction_failed`(문서 수). 세션 전용 규칙은 기존 그대로(ADR-034).
7. `openarchive reextract <id>`가 OCR 대상 문서를 만나면 "텍스트 인식 대기"로 세고 출력에 드러낸다(`reextract_all` 요약 포함).

### 2) 구현

- `backend/app/services/documents.py`에 `class ExtractionInProgress(Exception)` — 메시지는 사용자용 한국어
  (예: `"텍스트를 인식하는 중에는 이 작업을 할 수 없습니다. 인식이 끝난 뒤 다시 시도하세요."`), `main.py`에서 409로 매핑.
  빈 본문 `failed` 편집 거부도 409지만 메시지는 벗어나는 방법(원본 교체·다시 추출)을 알려준다.
- 판정 위치: 권한 확인(403/404) **뒤**, 버전 비교 **앞**. 문서 행을 잠근 트랜잭션 안에서 읽은 값으로 판정한다
  (워커 반영과 경합해도 둘 중 하나만 이긴다).
- 재추출·교체의 OCR 대상 판정은 `needs_ocr(content_type, extract_text(...))`. OCR 대상이면 텍스트를 쓰지 않고
  `UPDATE documents SET extraction_status = 'pending'`(교체는 filename·content_type 갱신과 같은 문장이어도 된다) — 잡은
  트리거가 만든다. 이 UPDATE의 SET 절에 `content_hash`를 넣지 마라.
- `system.py`: 상태 집계에 두 카운트 추가, `ReextractSummary`에 인식 대기 수 추가. CLI 출력에 반영.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_documents_api.py tests/test_documents.py tests/test_system.py tests/test_system_api.py tests/test_cli.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: 주체 문서 열람 검증이 서비스(`ensure_visible`·`_load_for_write`)에 남아 있는가(CLAUDE.md CRITICAL)?
   `/api/admin/*`가 여전히 세션 전용인가(ADR-034)? MCP 서버(`backend/mcp_server/server.py`)가 서비스를 재사용해 같은
   차단을 자동으로 받는가(새 코드 없이)?
3. `phases/m19-ocr/index.json`의 step 4를 갱신한다.

## 금지사항

- 추출 중 문서의 편집을 "워커 결과로 덮어쓰기"로 허용하지 마라. 이유: 사람이 고친 텍스트가 사라진다(ADR-052 결정 7).
- 재추출·교체 요청 안에서 `ocr_text`를 부르지 마라. 이유: ADR-052 결정 3.
- 앱에서 `embedding_jobs`에 INSERT하지 마라. 이유: CLAUDE.md CRITICAL.
- 워커를 고치지 마라. 이유: step 5의 범위다.
- 기존 테스트를 깨뜨리지 마라
