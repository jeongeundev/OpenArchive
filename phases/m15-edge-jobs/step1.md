# Step 1: worker-edge-jobs

워커가 잡을 **종류별로** 처리한다. `kind='embed'`는 지금과 같고, `kind='edges'`는 자기 트랜잭션에서
`SELECT rebuild_document_edges(<document_id>)`를 돌린다. 관계 판정이 실패해도 청크와 `ready`는 남는다.

## 읽어야 할 파일

- `/docs/ARCHITECTURE.md` — 「워커 처리 루프」·「정합성 보장」·「관계 생성 트리거」
- `/docs/ADR.md` — ADR-009(폴링 주 경로) · ADR-015(버전 일관성·최신 수렴) · ADR-029 결정 3·6 · ADR-038(프로세스 생사는 워커 책임이 아니다)
- `backend/app/worker.py` — **전부 읽어라.** `claim_job`·`load_document`·`finalize_job`·`fail_job`·
  `release_job`·`sweep_zombies`·`process_once`·`drain`·`_listen_for_jobs`·`run_worker`
- `backend/migrations/016_edge_jobs.sql` — **step 0의 산출물**(번호가 다르면 step 0 요약을 보라).
  `kind` 열·`uq_pending_job_per_doc_kind`·잡만 만드는 `build_document_edges()`
- `backend/migrations/014_edges_triggers.sql` — `rebuild_document_edges(uuid)` 본체
- `backend/tests/test_worker.py` — 이 파일에 테스트를 추가한다. `DOC_V1`·`ExplodingProvider`·
  `conn`/`other_conn` 픽스처를 재사용하라
- `backend/tests/conftest.py` — `process_all_embedding_jobs`(169행)·`run_embedding_worker`(179행).
  **다른 테스트 약 90곳이 이 두 헬퍼로 워커를 돌린다** — `process_once`가 관계 잡까지 집으면
  그 테스트들은 고치지 않아도 초록으로 돌아온다

## 작업

### 1) 테스트 먼저 — `backend/tests/test_worker.py`

1. `test_an_edge_job_is_processed_in_its_own_transaction` — 문서를 ready까지 처리한 뒤
   (`process_once`를 두 번: 임베딩 잡 → 관계 잡) `document_edges`가 생기고 관계 잡이 `done`이다.
2. `test_a_failing_edge_job_leaves_chunks_and_ready_intact` — **이 phase의 핵심 주장**이다.
   관계 판정이 실패하도록 만들고(예: `rebuild_document_edges`를 같은 시그니처의 예외 던지는 함수로
   `CREATE OR REPLACE` — 테스트 트랜잭션 안에서 바꾸고 원복하라. `test_worker.py:250`의
   `test_slow_edges` 트리거가 같은 수법을 쓴다) 관계 잡을 처리하면:
   `document_chunks`는 그대로, `documents.embedding_status = 'ready'` 그대로, 관계 잡만
   `pending`(재시도 예약) 또는 소진 시 `error`.
3. `test_an_exhausted_edge_job_does_not_mark_the_document_as_error` — 위를 `MAX_ATTEMPTS`번 반복해
   관계 잡이 `error`가 되어도 `documents.embedding_status`는 `'ready'`다.
   대비되는 기존 동작(임베딩 잡 소진 → 문서 `error`)은 그대로여야 하므로 그 테스트도 확인하라.
4. `test_an_edge_job_is_discarded_when_the_document_is_being_reembedded` — 관계 잡을 claim하기 전에
   문서를 수정해 `embedding_status`가 `'pending'`이 된 상태를 만들고, 관계 잡을 처리하면
   `document_edges`를 건드리지 않고 잡이 `done`으로 마감된다. 이유: 새 ready 전이가 새 관계 잡을 만든다.
5. `test_sweep_recovers_both_job_kinds` — `kind='embed'`와 `kind='edges'` 좀비를 각각 만들고
   (`started_at`을 직접 과거로 UPDATE) `sweep_zombies`가 둘 다 회수한다. 단 소진된 관계 잡은
   문서를 `error`로 만들지 않는다.
6. `test_drain_processes_the_edge_job_after_the_embedding_job` — `drain` 한 번으로 임베딩 잡과
   그것이 만든 관계 잡이 **모두** 처리되어 edge까지 생긴다(FIFO, 우선순위 없음).
7. 기존 `test_finished_at_records_the_end_of_the_finalize_transaction`(253행)의 전제가 바뀐다 —
   이제 `finalize_job` 트랜잭션에 관계 판정이 들어 있지 않다. 이 테스트는 자기 트리거를 만들어 쓰므로
   그대로 통과해야 한다. **통과하는지 확인하고, 만약 깨지면 테스트의 의도(= `finished_at`이
   `now()`가 아니라 `clock_timestamp()`다)를 유지한 채 고쳐라.**

### 2) 구현 — `backend/app/worker.py`

시그니처 수준 지시:

```python
@dataclass(frozen=True)
class ClaimedJob:
    job_id: int
    document_id: UUID
    kind: str            # 'embed' | 'edges'

async def claim_job(conn) -> ClaimedJob | None:      # kind를 함께 RETURNING
async def finalize_edge_job(conn, job: ClaimedJob) -> bool:   # 반영했으면 True, 폐기했으면 False
```

규칙(벗어나면 안 된다):

- **`claim_job`은 종류를 가리지 않고 id 순으로 하나 집는다.** 우선순위 열을 만들지 마라.
  다만 `documents.embedding_status = 'processing'`으로 바꾸는 UPDATE는 **`kind='embed'`일 때만** 한다.
  이유: 관계 계산 중에 문서가 "처리 중" 배지로 돌아가면 사용자에게는 재임베딩으로 보인다.
- **`finalize_edge_job`**은 자기 트랜잭션에서:
  `SELECT 1 FROM documents WHERE id=%s FOR UPDATE` → 행이 없으면 `False`(삭제됨, CASCADE로 잡도 없다) →
  `embedding_status <> 'ready'`면 잡을 `done`으로 마감하고 `False`(재임베딩 중 — 새 ready가 새 잡을 만든다) →
  그 외에는 `SELECT rebuild_document_edges(%s)` 후 잡을 `done`, `finished_at = clock_timestamp()`.
  `documents`는 **절대 UPDATE하지 않는다.**
- **`process_once`**는 claim한 잡의 `kind`로 분기한다. `edges`면 `load_document`·`chunk_text`·`embed`를
  타지 않는다(모델을 부르지 않는다). 예외는 지금처럼 `fail_job`이 받는다.
- **`fail_job`·`release_job`·`sweep_zombies`**: "다른 pending 잡이 있는가" 판정은 **같은 kind 안에서만**
  본다(`AND kind = <job.kind>`). 소진 시 `documents.embedding_status='error'`로 떨어뜨리는 것은
  **`kind='embed'`일 때만** 한다. `sweep_zombies`의 CTE에서 `row_number() OVER (PARTITION BY ...)`도
  `document_id, kind`로 나눠야 한다 — 그러지 않으면 한 문서의 embed 좀비와 edges 좀비 중 하나가
  이유 없이 `done`으로 마감된다.
- 모듈 docstring과 `ARCHITECTURE.md` 참조 문구는 **이 step에서 최소한만** 고친다(문서 갱신은 step 4).
  단 워커가 두 종류를 처리한다는 사실은 docstring 첫 문단에 한 줄 적어라.

### 3) 회귀 확인

⛔ **구현을 끝내기 전에 `pytest -q`(전체 스위트)를 돌리지 마라 — 현황 파악용으로도 돌리지 마라.**
step 0까지만 적용된 상태에서는 빨간불로 끝나는 것이 아니라 **끝나지 않는다.** 워커가 아직 `kind`를
모르므로 `kind='edges'` 잡을 임베딩 잡으로 처리하고, `claim_job`이 문서를 `processing`으로,
`finalize_job`이 다시 `ready`로 바꿔 `trg_build_document_edges`가 또 걸린다 → **새 관계 잡** →
`conftest.process_all_embedding_jobs`의 `while process_once(...)`가 영원히 돈다. 2026-09-22 실측:
테스트 DB가 54 MB → 180 MB 이상으로 불어나 Docker VM 디스크를 채웠고 PostgreSQL이
`No space left on device`로 죽었다. **이 step의 kind 분기가 바로 그 루프를 끊는 것**이므로,
전체 스위트는 `process_once`의 분기를 넣은 뒤에 처음 돌린다.

`ready` 전이로 edge가 생기는 것에 기대던 테스트(검색·관련 문서·군집·진단·API)는 대부분
`process_all_embedding_jobs`가 관계 잡까지 드레인하면 저절로 초록으로 돌아온다. 그래도 남는 것이
있으면 테스트가 기대하는 것이 "ready 직후 edge"인지 확인하고, **테스트를 지우지 말고** 워커를 한 번 더
돌리도록(또는 `rebuild_document_edges`를 명시 호출하도록) 고쳐라.

## Acceptance Criteria

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q          # 전체 초록이어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. **전체 스위트가 초록이 아니면 이 step은 완료가 아니다.**
2. 아키텍처 체크리스트:
   - 애플리케이션이 `embedding_jobs`에 INSERT하지 않는가? (워커는 UPDATE만 한다)
   - 관계 판정이 `rebuild_document_edges` 한 곳에 남아 있는가? 워커가 판정 SQL을 복제하지 않았는가?
   - 폴링이 주 경로인가? LISTEN 없이도 관계 잡이 처리되는가? (ADR-009)
   - `finalize_job`(임베딩) 트랜잭션에서 관계 판정이 빠졌는가?
3. `phases/m15-edge-jobs/index.json`의 step 1을 갱신한다:
   - 성공 → `"status": "completed"`, `"summary"`에 추가한 함수명과 kind 분기 지점
   - 3회 실패 → `"status": "error"` / 사용자 개입 필요 → `"status": "blocked"` 후 중단

## 금지사항

- **관계 잡 처리에 임베딩 프로바이더를 부르지 마라.** 이유: 관계는 이미 저장된 청크 벡터로만 계산한다.
  모델을 부르면 관계 잡이 임베딩만큼 느려지고 `fake`/`local` 설정에 의존하게 된다.
- **관계 잡 실패로 `documents.embedding_status`를 바꾸지 마라.** 이유: 청크는 멀쩡해 검색이 되는데
  화면에 "임베딩 실패" 배지가 뜬다. 관계 미반영은 step 2의 별도 카운터가 관측한다.
- **관계 잡에 우선순위·전용 큐·전용 워커 프로세스를 만들지 마라.** 이유: 이슈가 요구한 것은 트랜잭션
  분리뿐이다. 임베딩 뒤에 관계가 늦게 붙는 구간은 ADR-029가 아웃박스의 대가로 적어 둔 것이며,
  그 지연을 없애려는 최적화는 근거(실측) 없이 넣지 않는다.
- **kind 분기를 넣기 전에 전체 스위트를 돌리지 마라.** 이유: 위 ⛔의 무한 루프가 개발 DB를
  디스크까지 채워 죽인다. 실측으로 확인된 사고이며 추측이 아니다.
- **`rebuild_document_edges`를 Python으로 다시 구현하지 마라.** 이유: 판정은 DB 안에 있어야 하고
  (이 과제의 심사 핵심), `openarchive rebuild-edges`와 같은 함수를 써야 결과가 일치한다.
- 기존 테스트를 깨뜨리지 마라.
