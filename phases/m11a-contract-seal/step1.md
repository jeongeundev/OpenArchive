# Step 1: system-service

## 배경 — 관측 가능성이 라우터 안에 갇혀 있다

PRD §5 C2는 정합성의 **관측 가능성을 독립된 제품 표면으로 승격**했다. 그런데 그 관측을 만드는
26줄 SQL(잡 카운터 CTE + `c.version <> d.version` 정합성 검증 + `inet_server_addr()`)이
`backend/app/api/system.py:40-67`의 라우터 함수 몸통 안에 있다. 결과는 두 가지다.

1. **Web UI 말고는 이 관측을 쓸 경로가 없다.** MCP도, 앞으로 붙을 프로그래매틱 인터페이스도
   서비스 계층을 소비하는데, 이 쿼리는 서비스에 없다.
2. **계층 규칙의 유일한 예외다.** `CLAUDE.md`는 *"백엔드 비즈니스 로직은 `backend/app/services/`에
   두고, API 라우터와 MCP 서버는 이를 재사용만 한다"*고 못박았다. `backend/app/api/` 전체에서
   `execute(`/`cursor(`가 나오는 파일은 `system.py` 하나뿐이고, `schemas.py` 밖에서 pydantic
   모델을 정의하는 파일도 이것뿐이다.

이 step은 **동작을 바꾸지 않는다.** 순수 이동이다. 인증 요구(401)는 step 2에서 별도로 한다.

### ✅ 결정 1 — DB가 모르는 두 값은 서비스가 인자로 받는다

**이 결정은 이미 닫혔다. 다시 판단하지 말고 아래대로 진행하라.**

응답 7필드 중 `zombie_timeout_minutes`(설정값 에코)와 `embedding_provider`(DI로 주입된
프로바이더 이름)는 SQL에서 나오지 않는다. 서비스가 이 둘을 **키워드 인자로 받아** 결과에
담는다. 라우터는 `.model_validate(result)` 한 줄로 끝난다.

```python
async def get_system_status(
    conn: psycopg.AsyncConnection,
    *,
    zombie_timeout_minutes: int,
    embedding_provider: str,
) -> SystemStatusResult:
```

라우터가 dataclass를 뜯어 다시 조립하는 방식(`SystemStatus(**asdict(result), embedding_provider=...)`)은
쓰지 마라 — 조립 책임이 다시 라우터로 돌아가고, `from_attributes=True`의 이점이 사라진다.

### ✅ 결정 2 — 응답 키 집합과 값은 한 글자도 바뀌지 않는다

`backend/tests/test_system_api.py:19-27`이 `set(body) == {...}`로 키 집합을 **정확히** 고정하고
`assert "reconnect_events" not in body`까지 단언한다. 그리고 `scripts/demo_recovery.sh`가
`jobs.pending`·`jobs.processing`·`inconsistent_documents`를 실 VM 데모의 증거 채널로 읽는다
(`:113-128`). **필드를 더하거나 이름을 다듬지 마라.**

## 읽어야 할 파일

- `backend/app/api/system.py` — 옮길 대상 전체(82줄). 인라인 모델(:15-31), SQL(:40-67),
  손으로 짠 행→모델 매핑(:68-82)
- `backend/app/services/diagnostics.py` — **이 step의 본보기다.** id 없는 집계 서비스가
  SQL 상수 → frozen dataclass → 라우터의 `.model_validate`로 이어지는 전형. `get_diagnostics`(:174)
- `backend/app/api/schemas.py` — 응답 모델 관례. 서비스 dataclass를 받는 모델은 클래스 몸통 첫
  줄에 `model_config = ConfigDict(from_attributes=True)`를 둔다. 중첩 모델도 각자 갖는다
  (`DuplicateDiagnostics`·`DiagnosticsResponse` :205-218 참조)
- `backend/app/api/deps.py` — `Connection` 별칭과 `get_embedding_provider`
- `backend/tests/test_system_api.py` — 깨뜨리면 안 되는 계약 전량
- `backend/tests/test_schemas.py` — 응답 스키마가 서비스 dataclass를 받는지 보는 테스트
- `backend/tests/test_diagnostics.py` — 집계 서비스의 테스트 관례

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_system.py`(신규)에 **서비스 직접 호출** 테스트를 쓴다. API 계약은
`test_system_api.py`가 계속 본다 — 여기서는 서비스가 단독으로 옳은지를 본다.

`test_links.py`의 구조를 그대로 따른다: `migrated_db`로 자체 async conn fixture를 만들고,
`conftest.insert_test_document`로 상태를 세팅하고, 서비스 함수를 키워드 인자로 부른다.

최소 세 개:

- 빈 DB에서 잡 카운터가 전부 0이고 `last_job_finished_at`이 `None`이다
- 문서를 넣으면 `pending`이 1이 되고, `conftest.process_all_embedding_jobs`로 처리하면 0으로
  돌아오며 `last_job_finished_at`이 채워진다
- 인자로 준 `zombie_timeout_minutes`·`embedding_provider`가 결과에 그대로 실린다
  (DB가 아니라 호출부에서 온 값임을 고정한다)

**이 시점에 실행하면 `ModuleNotFoundError`로 실패한다.** 그게 정상이다.

### 2) `backend/app/services/system.py`를 만든다

- SQL은 모듈 레벨 `SYSTEM_STATUS_SQL` 상수. `api/system.py:42-66`의 쿼리를 **그대로** 옮긴다.
  질의를 "개선"하지 마라 — 이 step은 이동이다.
- 반환은 frozen dataclass. 중첩 구조는 응답과 같게 유지한다:

```python
@dataclass(frozen=True)
class JobCounts:
    pending: int
    processing: int
    recovery_pending: int
    error: int


@dataclass(frozen=True)
class SystemStatusResult:
    node_address: str | None
    node_port: int
    jobs: JobCounts
    zombie_timeout_minutes: int
    last_job_finished_at: datetime | None
    inconsistent_documents: int
    embedding_provider: str
```

- 단일 CTE 한 방이므로 `conn.transaction()` 블록은 **필요 없다.** 현재도 없다. 넣지 마라.
- **주석에 반드시 남길 것**: `inconsistent_documents`가 세는 것은 *청크가 아니라 문서*라는 점
  (`count(DISTINCT c.document_id)`). `test_system_api.py`의 마지막 테스트가 이것을 고정한다.

### 3) 응답 모델을 `schemas.py`로 옮긴다

`JobCounts`·`SystemStatus` pydantic 모델을 `backend/app/api/schemas.py`로 이동하고, 둘 다
`model_config = ConfigDict(from_attributes=True)`를 붙인다. 파일의 기존 배치 관례를 따라
적당한 자리에 둔다.

서비스의 dataclass와 pydantic 모델의 이름이 겹치는 것(`JobCounts`)은 문제없다 — import 경로가
다르고, 이 저장소는 `Result`/`Response` 접미사로 계층을 구분해 왔다.

`backend/tests/test_schemas.py`에 "`SystemStatus`가 `SystemStatusResult`를 받는다"는 테스트를
이 파일의 기존 관례대로 추가한다.

### 4) 라우터를 위임만 남긴다

`backend/app/api/system.py`는 이렇게 줄어야 한다:

```python
@router.get("/status", response_model=SystemStatus)
async def get_system_status(
    conn: Connection,
    provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
) -> SystemStatus:
    result = await service.get_system_status(
        conn,
        zombie_timeout_minutes=get_settings().zombie_timeout_minutes,
        embedding_provider=provider.name,
    )
    return SystemStatus.model_validate(result)
```

- 라우터 함수 이름과 서비스 함수 이름이 같아 헷갈리면, 이 저장소의 관례대로
  `from app.services import system as service` 식으로 모듈을 import 해 `service.get_system_status`로
  부른다 (`api/documents.py`가 쓰는 방식).
- `psycopg.rows.dict_row` import가 라우터에서 고아가 되면 지운다.
- `/api/health`(`backend/app/main.py:79-81`)는 **건드리지 마라.** 이 step의 범위 밖이고,
  step 2에서 데모 스크립트가 이것에 의존하게 된다.

## Acceptance Criteria

```bash
cd backend

# 1) 새 서비스 테스트가 통과한다
.venv/bin/pytest tests/test_system.py -q
#   → 전부 passed

# 2) API 계약이 한 글자도 안 바뀌었다 (이 step의 회귀 검출기)
.venv/bin/pytest tests/test_system_api.py tests/test_schemas.py -q
#   → 전부 passed. 특히 set(body) == {...} 단언이 통과해야 한다

# 3) 라우터에 SQL이 남아 있지 않다
grep -c "execute(\|cursor(" app/api/system.py || echo "0건 — SQL 이동 완료"
#   → "0건 — SQL 이동 완료"

# 4) api/ 전체에 SQL이 없다 (계층 규칙 확인)
grep -rn "execute(\|cursor(" app/api/ || echo "0건 — api 계층에 직접 쿼리 없음"
#   → "0건 — api 계층에 직접 쿼리 없음"

# 5) 응답 모델이 schemas.py로 갔다
grep -c "class SystemStatus\|class JobCounts" app/api/system.py || echo "0건 — 모델 이동 완료"
#   → "0건 — 모델 이동 완료"
grep -c "class SystemStatus\|class JobCounts" app/api/schemas.py
#   → 2

# 6) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `app/services/system.py`에 fastapi/starlette/pydantic import가 없는가? (서비스는 HTTP도
     응답 스키마도 모른다)
   - SQL이 모듈 레벨 상수 한 곳에만 있는가?
   - 응답 필드가 추가·삭제·개명되지 않았는가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 6에서 일괄 처리).
4. 결과에 따라 `phases/m11a-contract-seal/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **SQL을 손보지 마라.** 이유: 이 step은 이동이다. 질의를 바꾸면 회귀 원인이 이동인지 수정인지
  구별할 수 없게 된다.
- **응답 필드를 더하거나 이름을 바꾸지 마라.** 이유: `test_system_api.py:19-27`이 키 집합을
  정확히 고정하고, `demo_recovery.sh`가 실 VM에서 그 필드를 읽는다.
- **이 step에서 인증을 걸지 마라.** 이유: 401은 step 2의 작업이다. 여기서 함께 하면 리팩터링
  회귀와 인증 변경이 한 커밋에 섞인다.
- **서비스에서 `get_settings()`를 직접 읽지 마라.** 이유: `embedding_provider`는 DI로만 알 수
  있어 어차피 인자다. 한쪽만 인자로 두면 두 값의 출처가 달라져 읽는 사람이 헷갈린다.
- **`conn.transaction()` 블록을 새로 만들지 마라.** 이유: 단일 CTE 한 문장이라 이미 원자적이다.
- **`/api/health`를 건드리지 마라.** 이유: step 2에서 데모 스크립트가 이것에 의존하게 된다.
- **기존 테스트를 깨뜨리지 마라.**
