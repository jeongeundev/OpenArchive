# Step 2: status-edge-counter

관계가 아직 반영되지 않은 문서 수를 `/api/admin/status`가 센다. 임베딩 잡 카운터는
`kind='embed'`만 세도록 좁힌다 — 지금은 관계 잡이 섞여 "대기 중 임베딩"이 부풀어 보인다.

## 읽어야 할 파일

- `/docs/ARCHITECTURE.md` — 「정합성 보장」(정합성 카운터가 무엇을 세는지)
- `/docs/ADR.md` — ADR-015(보장 범위: 버전 일관성 + 최신 수렴) · ADR-034(`/api/admin/*`는 세션 전용) · ADR-029
- `backend/app/services/system.py` — `SYSTEM_STATUS_SQL`·`JobCounts`·`SystemStatusResult`·
  `get_system_status`·`rebuild_all_edges`
- `backend/app/api/schemas.py` — `SystemStatus`(114행 부근, `inconsistent_documents`)
- `backend/app/cli.py` — `init` 마지막의 상태 출력(468~471행 부근, "원본과 어긋난 문서 N건")
- `backend/migrations/016_edge_jobs.sql` — **step 0 산출물**. `kind` 열
- `backend/app/worker.py` — **step 1 산출물**. 관계 잡이 언제 `done`/`error`가 되는지
- `backend/tests/test_system.py`·`backend/tests/test_system_api.py` — 여기에 테스트를 추가한다

## 작업

### 1) 테스트 먼저

`backend/tests/test_system.py`:

1. `test_stale_edge_documents_counts_documents_whose_edge_job_is_not_done` — 문서를 ready까지만 만들고
   (관계 잡은 pending) `stale_edge_documents == 1`. 워커가 관계 잡을 처리하면 `0`으로 돌아온다.
2. `test_stale_edge_documents_includes_failed_edge_jobs` — 관계 잡을 `error`로 만든 문서도 센다.
   **이유를 테스트 docstring에 적어라**: 격리했다고 어긋남을 숨기면 계약이 거짓말이 된다
   (`sweep_zombies`의 소진 처리와 같은 원칙).
3. `test_job_counters_only_count_embedding_jobs` — `kind='edges'` pending 잡이 있어도
   `jobs.pending`이 그것을 세지 않는다.
4. `test_empty_database_has_no_stale_edges` — 빈 DB에서 `0`.

`backend/tests/test_system_api.py`:

5. 응답 JSON에 `stale_edge_documents` 키가 있고 세션 인증이 필요하다(기존 인증 테스트 방식을 따라라).

### 2) 구현

**`backend/app/services/system.py`** — `SYSTEM_STATUS_SQL`에 CTE 하나를 더한다:

```sql
, stale_edges AS (
  SELECT count(DISTINCT document_id) AS stale_edge_documents
  FROM embedding_jobs
  WHERE kind = 'edges' AND status <> 'done'
)
```

`job_counts` CTE의 집계에는 `WHERE kind = 'embed'`를 건다(`recovery_pending`·`last_job_finished_at`
포함). `SystemStatusResult`에 `stale_edge_documents: int`를 더한다.

**`backend/app/api/schemas.py`** — `SystemStatus`에 `stale_edge_documents: int`.

**`backend/app/cli.py`** — `init`의 상태 출력에 한 줄:
`  관계 미반영 문서 {status.stale_edge_documents}건`. 문구는 "관계가 아직 계산되지 않은 문서"를 뜻하며
**"실시간"·"항상 최신" 같은 말을 쓰지 마라** (ADR-015).

`rebuild_all_edges`는 그대로 둔다 — 전량 재계산은 잡을 거치지 않고 함수를 직접 부른다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 비즈니스 로직이 `backend/app/services/`에 있는가? 라우터가 SQL을 직접 들고 있지 않은가?
   - `/api/admin/*`가 세션 전용으로 남아 있는가? (ADR-034 — 토큰으로 열지 마라)
   - 사용자 대상 문구에 "항상 최신"·"실시간 동기화"가 없는가? (ADR-015)
3. `phases/m15-edge-jobs/index.json`의 step 2를 갱신한다(성공/error/blocked는 step 0과 같은 규칙).

## 금지사항

- **관계 잡을 `document_edges` 행 수로 대신 세지 마라.** 이유: 이웃이 없어 edge가 0행인 문서와
  아직 계산되지 않은 문서가 구분되지 않는다. 세는 근거는 잡의 상태다.
- **`inconsistent_documents`의 의미를 바꾸지 마라.** 이유: 그것은 `document_chunks.version <>
  documents.version`(원본-벡터 어긋남)이고 화면 문구가 그렇게 적혀 있다. 관계는 **별도 카운터**다.
- **새 라우터·새 엔드포인트를 만들지 마라.** 이유: 기존 `/api/admin/status` 응답에 필드 하나가 느는 것이다.
- 기존 테스트를 깨뜨리지 마라.
