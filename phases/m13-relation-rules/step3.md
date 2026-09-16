# Step 3: worker-clock

## 배경 — 잡 완료 시각이 트랜잭션 시작 시각이다

`backend/app/worker.py`는 `embedding_jobs.finished_at = now()`로 잡을 마감한다. PostgreSQL의
`now()`는 **트랜잭션 시작 시각**이다. `finalize_job`은 청크 교체 → `embedding_status = 'ready'`
→ (AFTER 트리거 `trg_build_document_edges`가 관계 계산) → 잡 마감을 한 트랜잭션에서 하므로,
트리거에 40초가 걸려도 `finished_at`은 트랜잭션이 열린 순간이 찍힌다. #93 실측에서 159청크
문서의 잡이 11.9초로 기록됐지만 실제 트리거는 40초였다 — 잡 시간으로 트리거 비용을 추정하면
10배 이상 틀린다.

`clock_timestamp()`는 호출 시점의 실제 시각이다. `finished_at` 대입 전부를 이것으로 바꾼다.

이 step은 **워커 파일 하나**만 바꾼다. step 2(`014_edges_triggers.sql`)는 이미 적용되어 있고,
이 step과는 파일이 겹치지 않는다.

## 읽어야 할 파일

- `CLAUDE.md` — 워커는 폴링이 주 경로, `LISTEN`은 최적화
- `docs/ARCHITECTURE.md` 「워커 처리 루프」·「정합성 보장」
- `backend/app/worker.py` — **수정 대상.** `finished_at = now()`가 `finalize_job`(2곳)·`fail_job`(2곳)·
  `release_job`·`sweep_zombie_jobs`(CASE 식)·그 밖의 마감 경로에 있다. `grep -n finished_at`으로 전부 찾아라
- `backend/tests/test_worker.py` — **테스트 추가 대상.** 픽스처 `conn`·`other_conn`, 헬퍼
  `insert_document`, `claim_job`·`finalize_job` 호출 방식은 기존 테스트(`test_drain_processes_a_new_document_end_to_end`,
  `test_finalize_discards_a_stale_result_but_completes_the_job`)를 따른다
- `backend/migrations/008_edges_triggers.sql` · `014_edges_triggers.sql` — 트리거가 같은 트랜잭션 안에서 도는 구조

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_worker.py`

`test_finished_at_records_the_end_of_the_finalize_transaction`:

1. 문서 1건을 넣고 잡을 claim한다.
2. 트랜잭션 안에서 시간이 흐르게 만든다 — 테스트 안에서 **임시 AFTER 트리거**를 건다:
   ```sql
   CREATE FUNCTION test_slow_edges() RETURNS trigger LANGUAGE plpgsql AS $$
   BEGIN PERFORM pg_sleep(0.3); RETURN NEW; END $$;
   CREATE TRIGGER trg_test_slow_edges AFTER UPDATE OF embedding_status ON documents
     FOR EACH ROW WHEN (NEW.embedding_status = 'ready') EXECUTE FUNCTION test_slow_edges();
   ```
   `migrated_db`는 테스트마다 새 DB이므로 정리하지 않아도 된다.
3. `finalize_job` 호출 **직전**에 `SELECT clock_timestamp()`를 같은 연결에서 읽어 `before`로 둔다.
4. `finalize_job(...)`이 `True`를 반환한 뒤 `SELECT finished_at FROM embedding_jobs WHERE id = %s`를
   읽어 **`finished_at - before >= 0.3초`**를 단언한다. `now()`였다면 `finished_at`은 트랜잭션
   시작 시각이라 `before`와 거의 같아 실패한다.

같은 방식으로 실패 경로 하나만 더: `test_fail_job_finished_at_is_the_actual_time` — 재시도 예산을
소진시켜 `error`로 마감되는 경로(`test_retries_exhaust_into_error_state`의 방식)에서, `fail_job`
직전 `before`를 읽고 `finished_at >= before`를 단언한다. 여기에는 트리거가 없어 차이가 작으므로
`>=`면 충분하다 — 목적은 이 경로도 `clock_timestamp()`로 바뀌었음을 고정하는 것이다.

### 2) 구현 — `backend/app/worker.py`

`finished_at` 대입의 `now()`를 **전부** `clock_timestamp()`로 바꾼다. `started_at = now()`(claim)와
`next_attempt_at = now() + …`(백오프)는 **그대로 둔다** — claim 트랜잭션은 짧고, 백오프 기준은
잡 시각이 아니라 현재 시각이면 된다. 바꾼 이유를 `finalize_job` 안 주석 한 줄로 남긴다:
*`now()`는 트랜잭션 시작 시각이라 같은 트랜잭션의 AFTER 트리거(관계 계산) 시간이 빠진다.*

## Acceptance Criteria

```bash
cd backend && .venv/bin/python -m pytest tests/test_worker.py -q
cd backend && .venv/bin/python -m pytest -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `backend/app/worker.py` 외의 애플리케이션 파일을 바꾸지 않았는가?
   - `grep -n "finished_at = now()" backend/app/worker.py`와 `grep -n "THEN now()" backend/app/worker.py`가 **비어 있는가?** (sweep의 CASE 식도 포함)
   - 새 테스트 2개가 실제 DB에서 도는가? (Mock 금지)
3. 결과에 따라 `phases/m13-relation-rules/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `started_at`·`next_attempt_at`·`created_at`을 바꾸지 마라. 이유: 요청받은 것은 완료 시각뿐이다.
  claim의 `now()`는 트랜잭션이 짧아 차이가 없고, sweep의 좀비 판정(`started_at < now() - …`)은 기준이 흔들리면 안 된다.
- 트리거·마이그레이션을 건드리지 마라. 이유: step 2가 끝났고 이 step은 워커 스코프다.
- 테스트에서 `pg_sleep`을 `time.sleep`으로 대체하지 마라. 이유: 트랜잭션 **안**에서 시간이 흘러야
  `now()`와 `clock_timestamp()`의 차이가 드러난다.
- 기존 테스트를 깨뜨리지 마라.
