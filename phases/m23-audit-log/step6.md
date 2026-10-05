# Step 6: actor-worker

워커가 텍스트 인식(OCR) 결과를 문서에 반영할 때 행위자를 「시스템(워커)」로 DB에 넘긴다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- 감사 로그는 DB 트리거가 쓴다(step 0·1). `document_versions`에 v2 이상이 INSERT되면 `text_updated`가 남는다. 행위자는 `backend/openarchive/services/audit.py`의 `set_actor(conn, *, actor, via, share_id=None)` 하나로 넘긴다(step 3, `set_config(..., true)`, autocommit 연결에서 트랜잭션 밖이면 `RuntimeError`).
- 재추출(이미 텍스트가 있는 문서의 OCR 재실행)은 워커의 `finalize_extract_job` → `services/documents.py`의 `apply_extracted_text`가 `UPDATE documents SET version = version + 1, content = …`로 새 텍스트 버전을 만든다. 이때 GUC가 비어 있으면 감사 행이 `actor NULL`·`db_role='openarchive'`로 남아 화면에 「직접 접속」처럼 보인다 — 사실과 다르다. 그래서 `via='worker'`로 넘겨 「시스템(텍스트 인식)」으로 표시되게 한다(`actor`는 NULL — 재추출을 요청한 사람은 워커가 알 수 없다).
- 첫 추출(빈 텍스트 → 첫 텍스트)은 버전을 올리지 않아 v1이 그대로라 `text_updated`가 생기지 않는다(step 1 트리거 조건 `version > 1`).
- **워커 연결은 autocommit이다.** `finalize_extract_job`은 `async with conn.transaction():` 안에서 잡 소유 확인과 `apply_extracted_text`를 한다. 행위자는 **그 블록 안에서** 건다.
- 워커의 다른 쓰기(임베딩·관계·잡 상태)는 감사 대상 테이블을 쓰지 않는다 — 건드리지 않는다.

## 읽어야 할 파일

- `backend/openarchive/services/audit.py` — step 3 산출물
- `backend/openarchive/worker.py` — `finalize_extract_job`(406행 근처), `lock_owned_job`
- `backend/openarchive/services/documents.py` — `apply_extracted_text`(579행 근처)
- `backend/tests/test_worker.py` — 추출 잡 테스트 헬퍼(OCR은 테스트에서 어떻게 대체하는지 확인)
- `backend/tests/test_audit.py` — 감사 행 조회 헬퍼
- `/docs/ADR.md` — ADR-052(OCR 추출 잡), ADR-055

## 작업

### 1) 테스트 먼저 — `backend/tests/test_worker.py`에 추가

1. 텍스트가 있는 문서를 재추출 대기(`extraction_status='pending'`)로 두고 `finalize_extract_job`에 다른 텍스트를 주면 `text_updated` 1행, `detail.version`=새 버전, `actor IS NULL`, `actor_via='worker'`.
2. 첫 추출(빈 텍스트 문서)은 `text_updated`를 남기지 않는다.
3. 같은 텍스트(해시 동일)면 `text_updated`가 없다.

### 2) 구현 — `worker.py`의 `finalize_extract_job`

- 트랜잭션 블록 안, `apply_extracted_text`를 부르기 전에 `await set_actor(conn, actor=None, via="worker")`.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_worker.py tests/test_architecture.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: `set_actor` 호출을 트랜잭션 블록 **밖**으로 옮기면 `RuntimeError`가, 지우면 테스트 1(`actor_via='worker'`)이 실패해야 한다.
3. `phases/m23-audit-log/index.json`의 step 6을 갱신한다.

## 금지사항

- `apply_extracted_text` 안에 `set_actor`를 넣지 마라. 이유: 서비스는 호출자가 누군지 모른다 — 행위자는 진입점(워커)이 정한다. REST·CLI와 같은 원칙이다.
- `SET openarchive.…`·`set_config(...)`를 직접 쓰지 마라 — `set_actor`만.
- 워커의 잡 소유·락 로직(`lock_owned_job`, lease)을 바꾸지 마라. 이유: ADR-050의 범위이며 이 step과 무관하다.
- 기존 테스트를 깨뜨리지 마라
