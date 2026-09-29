# Step 5: extract-worker

이 phase(#135)는 이미지·스캔 PDF를 워커 잡으로 OCR한다(**ADR-052** — 먼저 읽어라, 특히 결정 3·8). step 2의 트리거가
`kind='extract'` 잡을 만들고, step 3의 `apply_extracted_text`가 결과를 반영한다. 이 step은 **워커가 추출 잡을 처리**하게 한다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-052**, ADR-050(lease·heartbeat·`lock_owned_job`·락 상한), ADR-038(재시도 예산·스윕), ADR-009(폴링 주 경로)
- `phases/m19-ocr/index.json` — step 1~4 summary
- `backend/app/worker.py` — **전체**. 특히 `claim_job`·`process_once`·`finalize_job`·`finalize_edge_job`·`fail_job`·`sweep_zombies`·
  `lock_owned_job`·`bound_lock_wait`와 각 docstring의 경합 설명
- `backend/app/services/documents.py` — `apply_extracted_text`(step 3)
- `backend/app/services/parsing.py` — `ocr_text`·`detect_content_type`(step 1)
- `backend/tests/test_worker.py` — 워커 테스트 형식(실 DB, FakeProvider, lease 시나리오)

## 작업

### 1) 테스트 먼저 (`backend/tests/test_worker.py`, 실 DB + 실 tesseract)

1. `scan_tax_page1.jpg`를 서비스로 업로드 → `process_once` 한 번 → 추출 잡 `done`, 문서 `done`, 본문이 정답과
   (공백 제거·NFKC 후) `difflib` 비율 ≥ 0.85, v1 존재, `document_files.text_version == 1`, embed 잡 pending.
   이어서 `process_once` → `embedding_status == "ready"`(FakeProvider). 업로드부터 검색 가능한 상태까지의 관통이다.
2. 스캔 PDF(`scan_tax_pages.pdf`)도 1과 같이 끝난다.
3. 글자 없는 흰 PNG(테스트 안에서 Pillow로 생성)를 업로드 → `process_once` → 문서 `failed`, 잡 `done`, `attempts == 1`
   (재시도하지 않는다), 텍스트 버전·embed 잡 없음.
4. `ocr_text`가 예외를 던지게 하면(monkeypatch — OCR 함수 교체이지 DB 가짜가 아니다) 잡은 백오프로 pending에 돌아가고,
   예산(`MAX_ATTEMPTS`)을 다 쓰면 잡 `error`, 문서 `extraction_status == "failed"`. `embedding_status`는 건드리지 않는다.
5. 스윕이 예산 소진으로 `error`를 판정한 extract 잡도 문서를 `failed`로 만든다(`sweep_zombies` 경로).
6. 잡을 잃은 워커(`lost` 설정 또는 소유권이 바뀐 잡)는 결과를 반영하지 않는다 — 문서는 `pending` 그대로.
7. 처리 중 문서가 삭제되면 실패가 아니다(잡은 CASCADE로 사라지고 예외 없음).
8. 기존 embed·edges 잡 테스트가 그대로 통과한다.

### 2) 구현 (`backend/app/worker.py`)

- `EXTRACT_JOB_KIND = "extract"`. `process_once`에 분기를 추가한다:
  최신 원본 판(`document_files`의 `file_version` 최대)의 파일명·바이트를 읽고 →
  `await asyncio.to_thread(ocr_text, data, detect_content_type(filename))` → `lost`면 버림 →
  `finalize_extract_job(conn, job, text)`.
- `finalize_extract_job`: 한 트랜잭션에서 `bound_lock_wait` → 문서 행 잠금 → `lock_owned_job`으로 소유 확인(아니면 아무것도
  쓰지 않음) → `apply_extracted_text` → 잡 `done`. 반영 결과가 `failed`여도 잡은 `done`이다(결정적 실패 — ADR-052 결정 8).
- `fail_job`·`sweep_zombies`: 예산 소진이 extract 잡이면 `UPDATE documents SET extraction_status = 'failed'
  WHERE id = … AND extraction_status = 'pending'`. embed 잡일 때의 `embedding_status='error'` 처리는 그대로 둔다.
- `claim_job`: extract 잡은 `embedding_status`를 `processing`으로 바꾸지 않는다(embed 전용 동작 유지).
- OCR은 CPU를 오래 쓴다 — 반드시 `asyncio.to_thread`. heartbeat가 lease를 연장하므로 긴 스캔도 소유가 유지된다.
- 모듈 docstring의 "잡은 두 종류다" 문단을 세 종류로 갱신한다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_worker.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: 워커가 폴링만으로 추출 잡을 드레인하는가(ADR-009 — NOTIFY는 최적화)? 반영·실패 기록이 `lock_owned_job`
   확인 트랜잭션 안에 있는가(ADR-050)? 앱이 `embedding_jobs`에 INSERT하지 않는가?
3. **판별력 확인**: 구현에서 (a) `to_thread` 대신 직접 호출, (b) 반영 결과 `failed`일 때 `fail_job` 호출, (c) 소유 확인 생략
   을 하나씩 되돌려 보고 어느 테스트가 실패하는지 확인한 뒤 원복한다. 안 잡히는 변형은 summary에 적는다.
4. `phases/m19-ocr/index.json`의 step 5를 갱신한다.

## 금지사항

- OCR 결과가 비었을 때 `fail_job`으로 재시도하지 마라. 이유: 같은 입력이면 같은 결과다 — 예산만 쓰고 늦게 `failed`가 된다.
- extract 잡 실패로 `embedding_status`를 `error`로 만들지 마라. 이유: 인식 실패와 임베딩 실패는 사용자 행동이 다르다(ADR-052 결정 4).
- 워커 안에서 OCR 모델 파일을 내려받거나 원격 OCR을 부르지 마라. 이유: 오프라인 구동([별표2]), ADR-003.
- 기존 테스트를 깨뜨리지 마라
