# Step 5: rebuild-cli

## 배경 — 트리거는 처리 시점까지 들어온 문서만 본다

`document_edges`는 문서가 `ready`가 될 때 그 문서 기준으로 계산된다(step 0의
`rebuild_document_edges(uuid)`를 트리거가 호출한다). 그래서 **먼저 들어온 문서는 나중에 들어온
문서를 이웃으로 잡을 기회가 없다.** 100건을 순서대로 넣으면 첫 문서의 이웃은 "그때까지 있던
0건" 중에서, 열 번째 문서의 이웃은 9건 중에서 고른 것이다. #94 시뮬레이션에서 순차 적재 결과와
전체 코퍼스 기준 결과의 문서쌍 일치는 **0.37~0.63**이었고, 적재 순서를 섞으면 결과의 31~49%만
겹쳤다. 단방향 저장(step 0)은 남의 발견을 지우지 않게 했을 뿐 이 순서 의존은 풀지 못한다.

순도(덩어리가 분류와 맞는 정도)는 순서에 둔감했다(C 0.577 vs 전체 기준 0.587). 그러나 **어느
쌍이 이어지는가**는 흔들리므로, 대량 적재 뒤 한 번 **모든 문서를 전체 코퍼스 기준으로 다시
계산**하는 명령이 필요하다. 각 문서를 `rebuild_document_edges`로 재처리하면 자기 행만 교체되고
결과는 적재 순서와 무관하게 수렴한다. 비용은 P1 적용 뒤 청크당 약 14ms — 2,211청크 코퍼스에서
30초 안팎이다.

트리거 안에서 이웃 문서를 역방향으로 재계산하는 방안은 기각됐다(비용이 이웃 청크 수에 비례해
`finalize_job`이 5배 길어지고, kNN 비대칭이라 그래도 완전하지 않다).

## 읽어야 할 파일

- `CLAUDE.md` — "백엔드 비즈니스 로직은 `services/`에, CLI·라우터·MCP는 재사용만", "API 토큰·관리 API는 세션 전용"(이 CLI는 DB에 직접 붙는 운영 명령이라 해당 없음)
- `docs/ADR.md` **ADR-039**(`openarchive` CLI — init·serve) · **ADR-040**(`reset-password` — 웹에 두지 않은 이유) · **ADR-029**
- `backend/app/cli.py` — **수정 대상.** `main`의 서브커맨드 등록 방식, `run_reset_password`·`_reset`·`_ConnectionFailed`의 **연결 실패와 실행 실패를 구분하는 패턴**을 그대로 따른다
- `backend/app/services/system.py` — **추가 대상.** 기존 `get_system_status`의 스타일
- `backend/migrations/014_edges_triggers.sql` — `rebuild_document_edges(uuid)`의 계약(자기 src 행만 교체, 청크가 없으면 행 0)
- `backend/tests/test_cli.py` — **테스트 추가 대상.** `migrated_db`·`clean_db`·`capsys`·`_insert_user` 패턴, `test_reset_password_does_not_report_a_query_failure_as_a_connection_failure`
- `backend/tests/test_system.py` — **테스트 추가 대상**
- `backend/tests/test_triggers.py`의 `insert_document`·`mark_document_ready`·`unit_vector` — 문서를 ready로 만드는 가장 짧은 방법 (import해서 써도 되고 같은 방식으로 헬퍼를 두어도 된다)
- `backend/tests/conftest.py` — `process_all_embedding_jobs`

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_system.py`:

1. `test_rebuild_all_edges_lets_earlier_documents_see_later_ones` — first·second를 순서대로 ready
   (`unit_vector(0)` 같은 벡터). 트리거 결과는 `(second→first)`뿐이다. `rebuild_all_edges(conn)`를
   호출하면 반환값이 **2**(처리한 문서 수)이고 `(first→second)`도 생긴다. 다시 호출해도 행 집합이
   같다(멱등).
2. `test_rebuild_all_edges_skips_documents_that_are_not_ready` — `ready` 1건 + `pending` 1건(청크
   없음). 반환값 1, pending 문서의 src 행 0.
3. `test_rebuild_all_edges_commits_per_document` — 문서 3건 ready. `rebuild_all_edges`는 문서마다
   커밋하므로 호출 중간에 다른 연결에서 이미 처리된 문서의 행을 볼 수 있다 — 검증은
   `on_progress` 콜백으로: 콜백이 `(done, total)`을 문서마다 받고, 콜백 안에서 **다른 연결**로
   `count(*) WHERE src_document_id = 방금 문서`를 읽으면 0보다 크다.

`backend/tests/test_cli.py`:

4. `test_rebuild_edges_recomputes_every_ready_document` — 문서 2건 ready 뒤
   `main(["rebuild-edges", "--dsn", migrated_db])`가 0을 반환하고 `(first→second)`가 생기며,
   출력에 처리한 문서 수 `2`가 있다.
5. `test_rebuild_edges_reports_a_connection_failure_without_traceback` — 붙을 수 없는 DSN에서
   exit code 1, 출력에 `연결하지 못했습니다`, traceback 없음.
6. `test_rebuild_edges_does_not_report_a_query_failure_as_a_connection_failure` — `clean_db`
   (스키마 없음)에서 `psycopg.Error`가 그대로 올라오고 출력에 `연결하지 못했습니다`가 없다.

### 2) 구현 — `backend/app/services/system.py`

```python
async def rebuild_all_edges(
    conn: psycopg.AsyncConnection,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """ready 문서 전부를 전체 코퍼스 기준으로 다시 계산한다. 처리한 문서 수를 돌려준다.

    문서마다 따로 커밋한다 — 한 트랜잭션에 묶으면 수천 문서의 관계 삭제·삽입이 한 번에
    잠기고, 중간 실패가 전부를 되돌린다. 문서 하나의 재계산은 그 자체로 원자적이다
    (rebuild_document_edges가 DELETE→INSERT를 한 함수 안에서 한다).
    """
```

- 대상: `SELECT id FROM documents WHERE embedding_status = 'ready' ORDER BY created_at, id`.
- 문서마다 `SELECT rebuild_document_edges(%s)`를 **자기 트랜잭션**으로 실행한다(`conn`은
  autocommit이거나, 문서마다 `async with conn.transaction()`).
- `on_progress(done, total)`을 문서마다 부른다.

### 3) 구현 — `backend/app/cli.py`

`rebuild-edges` 서브커맨드(`--dsn`, 생략 시 `DATABASE_URL`). `run_rebuild_edges(*, dsn) -> int`:

- 연결 실패만 `_ConnectionFailed`로 잡아 `연결하지 못했습니다: …`를 출력하고 1을 반환한다.
  **실행 중 오류는 감싸지 않는다** — `_reset`의 docstring과 같은 이유.
- 진행은 `on_progress`로 `\r  {done}/{total}` 식으로 찍고, 끝에
  `관계를 다시 계산했습니다: 문서 {n}건`을 출력한다.
- help 문구: *모든 문서의 관계를 전체 코퍼스 기준으로 다시 계산합니다. 대량 적재 뒤 한 번 실행합니다.*

`docs/OPERATIONS.md`·`README.md`의 명령 목록 갱신은 step 7이 한다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/python -m pytest tests/test_system.py tests/test_cli.py -q
cd backend && .venv/bin/python -m pytest -q
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/openarchive rebuild-edges --help
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 마지막 명령은 help가 출력되고 exit 0이어야 한다.
2. 아키텍처 체크리스트:
   - 재계산 로직이 `services/system.py`에 있고 CLI는 호출만 하는가?
   - 애플리케이션이 `document_edges`에 직접 INSERT/DELETE하지 않는가? (DB 함수 호출만)
   - 연결 실패와 실행 실패의 보고가 구분되는가?
3. 결과에 따라 `phases/m13-relation-rules/index.json`의 step 5를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (함수 이름과 시그니처를 담아라 — step 6이 `seed_demo.py`에서 부른다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `/api/admin/*`에 재계산 엔드포인트를 만들지 마라. 이유: 요청받지 않았고, 수십 초 걸리는 작업을 HTTP 요청에 묶는 것은 별도 설계다.
- `embedding_status`를 흔들어(`pending`→`ready`) 트리거를 재발화시키는 방식으로 구현하지 마라. 이유: 상태 전이는 워커의 계약이고, `/admin/status` 카운터를 오염시킨다. 함수를 직접 호출한다.
- 전체를 한 트랜잭션에 묶지 마라. 이유: 위 docstring.
- `rebuild_document_edges`의 판정 로직을 Python으로 옮기지 마라. 이유: 관계는 DB 계층이 만든다(ADR-029 결정 3).
- 기존 테스트를 깨뜨리지 마라.
