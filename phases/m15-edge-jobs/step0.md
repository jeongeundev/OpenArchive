# Step 0: edge-jobs-schema

관계 판정을 임베딩 트랜잭션에서 떼어낸다. `ready` 트리거는 **관계 잡 한 행만** 만들고,
`document_edges`는 이 step에서 아무도 만들지 않는다 — 실제 계산은 step 1의 워커가 한다.

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라. 특히 002와 003은 이 step이 바꾸는 코얼레싱·아웃박스
규약의 원본이다.

- `/docs/ARCHITECTURE.md` — 「자동 임베딩 파이프라인 (DB 계층)」·「관계 생성 트리거」·「정합성 보장」
- `/docs/ADR.md` — ADR-001(아웃박스) · ADR-009(폴링 주 경로) · ADR-015(버전 일관성·최신 수렴) ·
  **ADR-029 결정 3**(관계를 ready 트리거가 만든다. `edge_jobs` 아웃박스를 "지금 기각"으로 남긴 문단이
  이 phase의 재검토 대상이다) · ADR-029 결정 6(전량 재계산)
- `backend/migrations/002_tables.sql` — `embedding_jobs` 정의와 `uq_pending_job_per_doc`
- `backend/migrations/003_triggers.sql` — `on_document_content_changed()`의 잡 INSERT·`pg_notify`
- `backend/migrations/008_edges_triggers.sql` — `trg_build_document_edges`(ready 전이 WHEN)
- `backend/migrations/014_edges_triggers.sql` — `rebuild_document_edges(uuid)`와 `build_document_edges()`
- `backend/app/migrations.py` — 러너(파일당 1트랜잭션, `schema_migrations`에 파일명으로 이력)
- `backend/tests/test_triggers.py` — 이 파일에 테스트를 추가한다. `insert_document`·`job_statuses`·
  `mark_document_ready`(85행) 헬퍼를 반드시 재사용하라

## 작업

### 1) 테스트 먼저 — `backend/tests/test_triggers.py`

기존 관계 테스트(517행 `test_ready_document_builds_edges_inside_the_database`부터 897행
`test_overlaps_requires_the_ratio_on_both_sides`까지)는 **ready 전이가 곧 edge**라는 옛 저장 의미에
기대고 있다. 저장 의미가 바뀌므로 **읽는 쪽을 먼저 고친다**:

- **판정 규칙을 검증하는 테스트**(kind 구분·1청크·2청크·양쪽 비율·양쪽 3대목·cap 5·HNSW 프로브 등)는
  `mark_document_ready(...)` 뒤에 **`SELECT rebuild_document_edges(<doc_id>)`를 명시적으로 호출**하도록
  바꾼다. 판정 규칙 자체는 이 phase에서 **한 글자도 바꾸지 않는다** — 같은 입력에 같은 edge가 나와야 한다.
- **`ready` 전이 자체를 검증하는 테스트**는 아래 새 단언으로 바꾼다.

새로 고정할 것(이름은 예시이며 의도를 지켜 지으면 된다):

1. `test_ready_transition_enqueues_an_edge_job_instead_of_building_edges` —
   `mark_document_ready` 직후 `document_edges`는 **0행**이고, `embedding_jobs`에 그 문서의
   `kind='edges'` `pending` 잡이 **1행** 있다.
2. `test_edge_jobs_coalesce_per_document_like_embedding_jobs` — 같은 문서를 두 번 `ready`로 전이시켜도
   (`UPDATE documents SET embedding_status='pending'` 뒤 다시 ready) `kind='edges'` pending 잡은 1행이다.
3. `test_an_embedding_job_and_an_edge_job_coexist_for_one_document` — 같은 문서에 `kind='embed'` pending과
   `kind='edges'` pending이 **동시에** 존재할 수 있다(유니크 인덱스가 kind를 포함한다).
   이 테스트가 인덱스를 `(document_id)`에서 `(document_id, kind)`로 바꾼 것을 고정한다.
4. `test_content_change_still_creates_an_embedding_job_with_the_default_kind` — 003 트리거의 INSERT는
   `kind`를 명시하지 않는다. 본문 수정으로 생긴 잡의 `kind`가 `'embed'`다(DEFAULT).
5. `test_ready_transition_notifies_the_same_channel` — ready 전이가 `embedding_jobs` 채널로 NOTIFY한다.
   기존 NOTIFY 테스트(`listener` 픽스처)와 같은 방식으로 확인하라.
6. `test_rebuild_document_edges_is_unchanged_and_still_scoped` — 810행
   `test_edges_trigger_definition_and_function_settings_are_scoped`를 유지·보강한다.
   `rebuild_document_edges`의 세 `SET`(`hnsw.ef_search`·`random_page_cost`·`enable_seqscan`)이 그대로이고,
   `trg_build_document_edges`의 정의가 여전히 `AFTER UPDATE OF embedding_status` + ready 전이 WHEN이다.

**금지**: `document_edges`에 직접 INSERT해서 테스트를 통과시키지 마라(기존 테스트가 픽스처로
INSERT하는 것은 그대로 둔다 — 그것은 조회 테스트의 입력이다).

### 2) 구현 — `backend/migrations/016_edge_jobs.sql` (새 파일)

> 파일 번호 주의: 이 저장소의 최신 마이그레이션은 `015_links_triggers.sql`이다. 016이 비어 있는지
> `ls backend/migrations/`로 **반드시 확인**하고, 이미 016이 있으면 그다음 번호를 쓴 뒤
> step 요약에 그 번호를 적어라.

내용은 셋이다.

```sql
ALTER TABLE embedding_jobs ADD COLUMN kind text NOT NULL DEFAULT 'embed';
ALTER TABLE embedding_jobs ADD CONSTRAINT embedding_jobs_kind_valid
  CHECK (kind IN ('embed', 'edges'));

DROP INDEX uq_pending_job_per_doc;
CREATE UNIQUE INDEX uq_pending_job_per_doc_kind
  ON embedding_jobs(document_id, kind) WHERE status = 'pending';

CREATE OR REPLACE FUNCTION build_document_edges() RETURNS trigger ...
```

`build_document_edges()`는 **더 이상 `rebuild_document_edges`를 부르지 않는다.** 대신:

- `INSERT INTO embedding_jobs (document_id, kind) VALUES (NEW.id, 'edges') ON CONFLICT DO NOTHING`
  — 충돌 대상을 명시하지 않는 것은 003과 같은 이유다(제약 정의를 복사해두면 어긋난다).
- `PERFORM pg_notify('embedding_jobs', NEW.id::text)` — 채널은 하나를 재사용한다. 워커의 wake 신호일
  뿐이고 페이로드로 분기하지 않는다 (ADR-009: 알림은 최적화이며 전달 보장 수단이 아니다).
- `RETURN NEW`.

`rebuild_document_edges(uuid)`와 `trg_build_document_edges` 트리거 정의는 **건드리지 마라.**
판정 본체는 그대로 남아 워커·`openarchive rebuild-edges`·테스트가 같은 함수를 쓴다.

파일 머리 주석에 적을 것: 왜 별도 테이블이 아니라 `kind`인가(claim·fail·release·sweep·CASCADE·
좀비 임계를 그대로 공유한다), 인덱스를 `(document_id, kind)`로 바꾼 이유(한 문서가 임베딩 잡과 관계
잡을 동시에 가질 수 있어야 한다), 012→015에서와 같은 "파일을 새로 두는" 이유(러너가 파일명으로 이력을
남기므로 기존 파일을 고치면 적용된 DB에 반영되지 않는다).

## Acceptance Criteria

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest tests/test_triggers.py tests/test_tables.py tests/test_indexes.py -q
```

> ⛔ **이 step에서 `pytest -q`(전체 스위트)를 돌리지 마라.** 빨간불로 끝나는 것이 아니라 **끝나지
> 않는다.** 워커는 아직 `kind`를 모르므로 `kind='edges'` 잡을 임베딩 잡으로 처리한다 →
> `claim_job`이 문서를 `processing`으로, `finalize_job`이 다시 `ready`로 바꾼다 →
> `trg_build_document_edges`의 WHEN(`OLD IS DISTINCT FROM 'ready' AND NEW = 'ready'`)이 또 걸려
> **새 관계 잡이 생긴다** → `conftest.process_all_embedding_jobs`의 `while process_once(...)`가
> 영원히 돈다. 2026-09-22 실측: 청크 DELETE·재삽입이 반복되며 테스트 DB가 54 MB → 180 MB 이상으로
>불어나 Docker VM 디스크(58 GB)를 채웠고, PostgreSQL이 `FileFallocate(): No space left on device`로
> 죽어 crash recovery까지 실패했다. 세션 하나가 30분 타임아웃으로 통째로 날아갔다.
> 전체 스위트는 **step 1이 워커에 kind 분기를 넣은 뒤** 처음 돌린다.
>
> `test_triggers.py`·`test_tables.py`·`test_indexes.py` 세 파일은 이 step에서 **전부 초록이어야 한다**.
> 이 세 파일은 워커를 돌리지 않으므로 위 루프에 걸리지 않는다. 다른 테스트(검색·관련 문서·군집·
> 진단·API)가 `ready` 전이로 edge가 생기는 것에 기대는 것은 step 1에서 해소된다. 그것을 미리
> 고치려고 `document_edges`에 직접 INSERT를 넣거나 테스트를 지우지 마라.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 스키마 변경이 `backend/migrations/`의 번호 붙은 raw SQL 하나로만 이뤄졌는가? (ORM 도구 금지)
   - 잡 생성이 여전히 **DB 트리거**인가? 애플리케이션 코드에서 `embedding_jobs`에 INSERT하지 않았는가?
   - 임시 테이블을 만들지 않았는가? (ADR-022 — OpenProxy가 `DISCARD ALL`을 하지 않아 누수된다)
   - `rebuild_document_edges`의 함수 정의 `SET` 세 개가 그대로인가? (ADR-029 결정 6)
3. `phases/m15-edge-jobs/index.json`의 step 0을 갱신한다:
   - 성공 → `"status": "completed"`, `"summary"`에 **마이그레이션 파일명**과, 전체 스위트를 아직
     돌리지 않았다는 사실(위 ⛔ 이유)을 적어라
   - 3회 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- **판정 규칙(`rebuild_document_edges` 본체)을 고치지 마라.** 이유: 이 phase는 *언제* 계산하는지만 바꾼다.
  비율·하한·cap·순서를 건드리면 #94에서 실측으로 고정한 관계 품질이 무효가 되고, 변경 전후 edge 집합
  동일 여부(이 이슈의 검증 1항)를 판정할 수 없게 된다.
- **`trg_build_document_edges` 트리거의 WHEN 조건을 바꾸지 마라.** 이유: ready → ready 재진입 방지와
  "모든 청크가 들어간 뒤 한 번"이 그 조건에 걸려 있다 (008 주석).
- **새 NOTIFY 채널을 만들지 마라.** 이유: 워커는 채널 하나로 깨어나면 충분하고, 채널이 둘이면
  `_listen_for_jobs`가 두 연결을 들어야 한다 — LISTEN은 OpenProxy 경유에서 동작하지 않는 최적화다.
- **`documents.embedding_status`에 관계용 상태값을 추가하지 마라.** 이유: 그 열은 임베딩 파이프라인의
  상태이고 UI 배지가 읽는다. 관계 진행 상황은 step 2가 잡 테이블에서 센다.
- **전체 스위트(`pytest -q`)를 돌리지 마라.** 이유: 위 ⛔에 적은 무한 루프가 개발 DB를 디스크까지
  채워 죽인다. 실측으로 확인된 사고이며 추측이 아니다.
- 기존 테스트를 깨뜨린 채 두지 마라 — 단 워커를 돌리는 테스트가 step 1까지 빨간 것은 예외다.
