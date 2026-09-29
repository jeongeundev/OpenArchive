# Step 2: extract-schema

이 phase(#135)는 OCR 추출을 **워커 잡**으로 한다. 이 step은 DB 계층만 다룬다: 추출 상태 컬럼, 추출이 끝난
문서에 한정된 빈 본문 제약, 추출 잡을 만드는 트리거. 결정은 **ADR-052** 결정 3~6이다 — 먼저 읽어라.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-052**(맨 끝) 전체, ADR-001(트랜잭셔널 아웃박스)
- `backend/migrations/002_tables.sql` — `documents_content_not_blank` CHECK와 그 주석(왜 문자셋을 명시했나)
- `backend/migrations/003_triggers.sql` — `on_document_content_changed()`·`trg_documents_content_changed`와 WHEN 조건의 이유
- `backend/migrations/016_edge_jobs_tables.sql`·`017_edge_jobs_triggers.sql` — 잡 종류(kind) 추가의 선례. 파일 머리 주석 형식
- `backend/migrations/018_files_tables.sql` — `document_files.text_version` FK
- `backend/tests/test_tables.py`·`backend/tests/test_triggers.py` — 제약·트리거 테스트 형식(실 DB, NOTIFY 수신 헬퍼)
- `backend/app/migrations.py` — 러너(파일명 순 적용, 적용 이력)

## 작업

### 1) 테스트 먼저

`backend/tests/test_tables.py`:
1. `documents.extraction_status` 기본값 `'done'`, `('pending','failed','done')` 밖의 값은 거부.
2. `extraction_status='done'`인 빈 본문(`E' \t\r\n\f'` 포함)은 여전히 거부된다(기존 테스트가 그대로 통과해야 한다).
3. `'pending'`·`'failed'`인 문서는 빈 본문으로 INSERT된다.
4. 빈 본문 `pending` 문서를 본문 없이 `'done'`으로 바꾸는 UPDATE는 거부된다.
5. `document_files.text_version`은 NULL을 허용한다. NULL이 아닌 값은 여전히 FK로 `document_versions`를 가리켜야 한다.
6. `embedding_jobs.kind`에 `'extract'`가 들어가고, 모르는 값은 거부된다. `(document_id, kind)`당 pending 1개 코얼레싱은 `extract`에도 적용된다.

`backend/tests/test_triggers.py`:
7. `pending` 빈 본문 INSERT → `document_versions` 0행, `kind='embed'` 잡 0개, `kind='extract'` pending 잡 1개, 커밋 시 NOTIFY(`embedding_jobs` 채널, 페이로드 문서 id).
8. `done` INSERT → 지금과 같다(v1 기록 + embed 잡). 기존 트리거 테스트가 그대로 통과해야 한다.
9. `pending` 문서를 `content`·`content_hash`·`extraction_status='done'`으로 한 문장에서 UPDATE(버전은 1 그대로) → v1이 그 텍스트로 기록되고 embed 잡이 생긴다.
10. `done` 문서(본문 있음)를 `extraction_status='pending'`으로만 UPDATE → extract 잡 1개, 새 텍스트 버전 없음, embed 잡 없음, 본문은 그대로.
11. `pending`으로 두 번 설정해도 extract pending 잡은 1개(코얼레싱). extract 잡과 embed 잡은 같은 문서에 동시에 pending일 수 있다.
12. `failed`로 바꾸는 UPDATE는 잡을 만들지 않는다.

### 2) 마이그레이션

**`backend/migrations/021_extract_tables.sql`** — 머리 주석에 ADR-052·#135와 "왜 조건부인가"를 적는다.
- `documents.extraction_status text NOT NULL DEFAULT 'done'` + `CHECK (extraction_status IN ('pending','failed','done'))`.
- `documents_content_not_blank`를 DROP하고 같은 이름으로 `CHECK (extraction_status <> 'done' OR length(btrim(content, E' \t\r\n\f')) > 0)`.
  문자셋은 002와 똑같이 명시한다(002 주석의 이유가 그대로 유효하다).
- `document_files.text_version`의 NOT NULL 제거. 주석: NULL = 이 판의 텍스트가 아직 추출되지 않았다, 워커가 v1을 쓰는 트랜잭션에서 채운다.
- `embedding_jobs_kind_valid`를 `('embed','edges','extract')`로 교체.

**`backend/migrations/022_extract_triggers.sql`**
- `trg_documents_content_changed`를 DROP/CREATE해 WHEN을 `(pg_trigger_depth() = 0 AND NEW.extraction_status = 'done')`로 바꾼다.
  함수 본체(`on_document_content_changed`)는 바꾸지 않는다. 주석: 추출 중 INSERT는 빈 v1과 헛 임베딩 잡을 만들지 않는다.
- 새 함수 `on_document_extraction_requested()` + 트리거 `trg_documents_extraction_requested`:
  `AFTER INSERT OR UPDATE OF extraction_status ON documents FOR EACH ROW WHEN (pg_trigger_depth() = 0 AND NEW.extraction_status = 'pending')`
  → `INSERT INTO embedding_jobs (document_id, kind) VALUES (NEW.id, 'extract') ON CONFLICT DO NOTHING` + `pg_notify('embedding_jobs', NEW.id::text)`.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_tables.py tests/test_triggers.py tests/test_migrations.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: 잡 생성이 전부 트리거 안인가(CLAUDE.md CRITICAL — 앱이 `embedding_jobs`에 INSERT하지 않는다)? `done` 문서의 빈 본문을 여전히 DB가 막는가?
3. `phases/m19-ocr/index.json`의 step 2를 갱신한다. summary에 컬럼·제약·트리거 이름을 적는다.

## 금지사항

- 002·003·016·017·018을 수정하지 마라. 이유: 적용된 마이그레이션은 러너 이력 때문에 고쳐도 기존 DB에 반영되지 않는다 — 새 파일로 바꾼다.
- `embedding_status`에 새 값을 끼우지 마라. 이유: 관계 트리거(`ready` 전이)·오류 문서 화면이 그 값을 읽는다(ADR-052 결정 4).
- `pending` 문서의 본문이 비어 있어야 한다는 제약을 걸지 마라. 이유: 재추출 중인 기존 문서는 이전 텍스트를 유지한다(ADR-052 결정 6).
- 파이썬 코드(서비스·워커)를 고치지 마라. 이유: 다음 step들의 범위다. 이 step에서 기존 테스트가 깨지면 스키마 쪽을 고친다.
- 기존 테스트를 깨뜨리지 마라
