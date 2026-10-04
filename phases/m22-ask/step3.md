# Step 3: ask-api

`POST /api/ask` — 커넥션 한 번 대여로 인증·근거 수집을 끝내고 반납한 뒤 생성한다. 모델이 꺼져 있거나 실패해도 검색 결과를 돌려준다.

## 읽어야 할 파일

- `/docs/ADR.md` — ADR-043 「구현 형태 (2026-10-04, #96 a)」, ADR-044 「공유」 결정 5(허용 목록), ADR-048(일시 불가용·재시도 미들웨어), ADR-034(토큰)
- `/docs/ARCHITECTURE.md` — 「API 설계」, 「근거 기반 답변 (POST /api/ask)」(step 0)
- `backend/openarchive/services/answer.py`(step 2), `backend/openarchive/answers/`(step 1)
- `backend/openarchive/api/deps.py` — `get_conn`·`Connection`(scope="function" — 요청 함수가 끝날 때까지 커넥션을 쥔다), `current_user`, `reject_share`, `require_user_id`
- `backend/openarchive/api/search.py`, `backend/openarchive/api/schemas.py`(`SearchRequest`·`SearchResult`), `backend/openarchive/main.py`(lifespan·`include_router`)
- `backend/openarchive/api/retry.py` — `is_retryable`
- `backend/openarchive/db.py` — `connection()`, `get_pool()`
- `backend/tests/test_search_api.py`, `backend/tests/test_share_access.py`(`SHARE_READABLE_ROUTES`, `test_only_auth_me_reads_the_principal_without_a_guard`), `backend/tests/test_retry.py`, `backend/tests/test_token_access.py`, `backend/tests/conftest.py`(`db_client`, `login_as`)

## 이 phase의 계약 (#96 a — 모든 step 공통, ADR-043 「구현 형태 (#96 a)」가 정본)

`ask`는 코어의 소비자인 고정 파이프라인이다: **검색 → 근거 조립 → 프롬프트 → 생성 → 응답**. DB에 테이블·트리거·컬럼을 추가하지 않고, 검색 SQL(`services/search.py`)과 열람 술어(`services/visibility.py`)는 **한 줄도 고치지 않는다**(이슈 #96 「코어 diff 0줄」). MCP에는 넣지 않는다(ADR-043 결정 5). 답은 저장하지 않는다(결정 4). 상용 API 프로바이더는 만들지 않는다(결정 2, 규정 [별표2]).

**설정** (`backend/openarchive/config.py`의 `Settings`, 환경변수는 대문자):

| 필드 | 타입·기본값 | 의미 |
|---|---|---|
| `answer_provider` | `Literal["off", "ollama", "fake"] = "off"` | 기본 꺼짐. `off`면 검색은 그대로, 답변만 `disabled` |
| `ollama_url` | `str = "http://localhost:11434"` | Ollama 서버 주소 |
| `answer_model` | `str = "qwen3:8b"` | Ollama 모델 태그. 임시 기본값 — 확정은 #96 c의 한국어 실측 |
| `answer_timeout_seconds` | `float`, `Field(default=120, gt=0)` | 생성 호출 한 번의 HTTP 타임아웃 |
| `answer_context_chars` | `int`, `Field(default=6000, gt=0)` | 프롬프트에 넣는 근거 본문의 글자 예산 |

**프로바이더** (`backend/openarchive/answers/`, `embeddings/`와 같은 모양):

```python
class AnswerUnavailable(Exception): ...          # 모델 호출 실패(연결 거부·타임아웃·HTTP 오류·빈 응답)를 하나로

class AnswerProvider(Protocol):
    name: str
    def generate(self, system: str, prompt: str) -> str: ...   # 동기. 서비스가 asyncio.to_thread로 부른다

def get_answer_provider(name: str | None = None) -> AnswerProvider | None   # "off" → None
```

**서비스** (`backend/openarchive/services/answer.py`) — DB 단계와 생성 단계를 **함수 둘로 나눈다**. 호출부는 첫 함수가 끝나면 커넥션을 반납하고 나서 둘째를 부른다(생성 동안 풀 커넥션 점유 금지):

```python
@dataclass(frozen=True)
class AnswerSource:
    label: int              # 1부터. 답 안의 [n]과 대응
    document_id: UUID
    title: str
    chunk_index: int
    based_on_version: int   # 이 대목이 나온 텍스트 버전
    current_version: int    # 문서의 현재 텍스트 버전(documents.version)
    revised: bool           # based_on_version < current_version
    content: str            # 모델에 준 대목 그대로
    cited: bool = False     # 답에 [label]이 실제로 나왔는가

@dataclass(frozen=True)
class Evidence:
    hits: list[SearchHit]           # 검색 결과 그대로
    sources: list[AnswerSource]     # cited=False 상태
    system: str
    prompt: str

AnswerStatus = Literal["answered", "no_evidence", "disabled", "failed"]

@dataclass(frozen=True)
class AnswerResult:
    status: AnswerStatus
    answer: str | None
    sources: list[AnswerSource]
    hits: list[SearchHit]
    detail: str | None

async def gather_evidence(conn, embedding_provider, *, query, user_id, tags=None, content_type=None,
                          k=ASK_K, context_chars) -> Evidence
async def generate_answer(evidence: Evidence, answer_provider: AnswerProvider | None) -> AnswerResult
```

**응답 상태**: `disabled`(프로바이더 None — 모델을 부르지 않는다) → `no_evidence`(근거 0건 — 모델을 부르지 않는다) → `answered` / `failed`(`AnswerUnavailable`). 어떤 상태든 `hits`(검색 결과)는 그대로 돌려준다. `AnswerUnavailable`이 아닌 예외는 코드 결함이므로 삼키지 않는다.

**API**: `POST /api/ask`, 본문 `{query, tags?, content_type?, k=5}`, 항상 200 + `{status, answer, detail, sources[], items[]}` (`items`는 `/api/search`의 `SearchResult`와 같은 모양). 빈 질의는 400. 인증은 로그인 사용자(세션·위임 토큰), **공유 주체는 403**(새 경로의 기본 — `tests/test_share_access.py`). 인증·검색·현재 버전 조회를 커넥션 **한 번 대여** 안에서 끝내고 반납한 뒤 생성한다 — 그래서 이 경로는 `api/deps.py`의 `Connection`·`current_user`·`require_user_id`(요청이 끝날 때까지 커넥션을 쥔다)에 기대지 않는다. 재시도 미들웨어(`api/retry.py`)의 재시도 대상에 `/api/ask`를 넣는다 — DB 작업이 생성보다 먼저 끝나므로 재시도해도 생성이 두 번 돌지 않는다.

**문구**: "정답"이 아니라 **"근거 기반 답변"**. "항상 최신"·"실시간"을 쓰지 않는다(CLAUDE.md). 근거 없음은 보장이 아니라 프롬프트 지시다.

## 작업

### 1) 테스트 먼저

`backend/tests/test_ask_api.py`(새 파일, `db_client` 위에서 — 실제 DB):
1. 로그인 사용자의 질문 → 200, `status == "answered"`(앱의 `app.state.answer_provider`를 `FakeAnswerProvider()`로 바꿔서), `sources[0]`에 `document_id`·`title`·`chunk_index`·`based_on_version`·`current_version`·`revised`·`content`·`cited`가 있고, `items`가 같은 질의의 `/api/search` `items`와 같은 문서 id 목록.
2. 미설정(`answer_provider = None`, 기본) → 200, `status == "disabled"`, `answer is None`, `items`는 검색 결과 그대로.
3. 실패하는 프로바이더(`AnswerUnavailable`) → 200, `status == "failed"`, `items` 그대로.
4. 근거 0건(존재하지 않는 태그) → `status == "no_evidence"`.
5. 빈 질의(`"  "`) → 400. 익명 → 401.
6. **생성 동안 풀 커넥션을 빌리지 않는다** — `generate`가 불릴 때 `get_pool().get_stats()`를 기록하는 프로바이더를 꽂고, 그 순간 `pool_size - pool_available == 0`(빌려 간 커넥션 0)을 단언한다. 같은 프로바이더로 기준선도 단언해 판별력을 확인한다: 라우터가 커넥션을 쥔 채 생성하도록 바꾸면(mutant) 이 테스트가 실패해야 한다 — 검증 절차 2.
7. 위임 토큰(Bearer, read scope)으로도 된다.

`backend/tests/test_share_access.py`:
- 공유 토큰으로 `POST /api/ask` → 403. `SHARE_READABLE_ROUTES`는 **바꾸지 않는다**(ask는 허용 목록 밖). 허용 목록 판정이 `require_reader`·`current_user` 의존성만 보기 때문에, 새 의존성을 쓰는 `/api/ask`가 판정에서 빠지는 구멍을 막는 단언을 추가한다 — 예: `current_user`를 직접 부르는 새 의존성도 `reject_share`를 거친다는 것을 공유 토큰 요청으로 확인하거나, 판정 함수가 새 의존성도 셈하게 넓힌다. 어느 쪽이든 "새 경로가 허용 목록 밖에서 공유를 여는데도 초록"이 남지 않게 한다.

`backend/tests/test_retry.py`:
- `/api/ask`가 `is_retryable`이 참이다(본문 재생 포함, `/api/search` 테스트와 같은 방식).

`backend/tests/test_main.py`(또는 lifespan을 보는 기존 테스트 위치):
- 기동 후 `app.state.answer_provider`가 설정을 따른다(`off` → `None`, `fake` → fake). 기동이 Ollama에 접속하지 않는다(`ollama`로 두고 닫힌 포트 url이어도 기동 성공).

### 2) 구현

- `backend/openarchive/api/deps.py`:
  - `async def released_current_user(authorization=Header(None), token=Cookie(None, alias=SESSION_COOKIE)) -> dict | None` — `async with connection() as conn:` 안에서 `current_user(conn, authorization, token)`을 불러 결과를 돌려준다. 커넥션은 이 함수가 끝날 때 반납된다. docstring에 이유(생성처럼 오래 걸리는 작업 동안 커넥션을 쥐지 않기 위해, ADR-043 구현 형태 1)를.
  - `async def require_released_user_id(user = Depends(released_current_user)) -> str` — `require_user_id`와 같은 판정(401·공유 403).
- `backend/openarchive/api/schemas.py` — `AskRequest(query: str, tags: list[str] | None = None, content_type: str | None = None, k: int = Field(default=5, ge=1, le=MAX_K))`, `AskSource`(계약의 `AnswerSource` 필드, `from_attributes`), `AskResponse(status: Literal[...], answer: str | None, detail: str | None, sources: list[AskSource], items: list[SearchResult])`.
- `backend/openarchive/api/ask.py` — `router = APIRouter(prefix="/api/ask", tags=["ask"])`, `POST ""`. 본문: 빈 질의 400 → `async with connection() as conn: evidence = await gather_evidence(conn, request.app.state.provider, ..., context_chars=get_settings().answer_context_chars)` → 블록 밖에서 `await generate_answer(evidence, request.app.state.answer_provider)` → `AskResponse`. `Connection` 의존성을 쓰지 않는다.
- `backend/openarchive/main.py` — lifespan에서 `app.state.answer_provider = get_answer_provider()`(예열·접속 없음), 기동 로그 한 줄(꺼짐 / `ollama <모델> @ <url>` / fake). `include_router(ask_router)`.
- `backend/openarchive/api/retry.py` — `/api/ask`를 재시도 대상에 넣고 docstring의 "POST /api/search는 메서드만 POST인 읽기" 문장에 ask를 함께 적는다(DB 단계가 생성보다 먼저 끝나 생성이 두 번 돌지 않는다).
- 프로바이더의 `ollama` 설정(url·model·timeout)은 `get_answer_provider`가 설정에서 읽는다(step 1).

## Acceptance Criteria

```bash
cd /Users/kje/00_Workspace/01_Coding/project/OpenSQL/backend
docker compose -f ../docker-compose.yml up -d
.venv/bin/pytest tests/test_ask_api.py tests/test_share_access.py tests/test_retry.py tests/test_main.py -q
.venv/bin/pytest -q
.venv/bin/ruff check .
git diff --quiet main -- openarchive/services/search.py openarchive/services/visibility.py openarchive/migrations openarchive/mcp_server && echo "코어·MCP diff 0"
cd .. && bash scripts/check.sh
```

## 검증 절차

1. AC를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① 라우터가 `Connection` 의존성(또는 `async with connection()` 블록 **안**)에서 `generate_answer`를 부르게 → 테스트 6 실패 ② `require_released_user_id`에서 공유 거절 삭제 → 공유 403 테스트 실패 ③ `is_retryable`에서 ask 삭제 → retry 테스트 실패. 하나라도 통과하면 테스트를 보강한다.
3. CLAUDE.md CRITICAL 확인: DSN 단일 엔드포인트(새 접속 경로 없음 — 풀만 씀), `BEGIN READ ONLY` 없음, MCP 무변경(ADR-043 결정 5), 토큰 발급·관리 경계 무변경.
4. `phases/m22-ask/index.json`의 step 3을 갱신한다. summary에 경로·응답 필드·mutant 결과·전체 테스트 수를 적는다.

## 금지사항

- `/api/ask`를 `SHARE_READABLE_ROUTES`에 넣지 마라. 이유: 공유에 여는 것은 ADR 개정 사항이다(ADR-043 구현 형태 6).
- MCP 서버에 ask 도구를 추가하지 마라. 이유: ADR-043 결정 5.
- 모델 미설정·실패를 503으로 주지 마라. 이유: 503은 DB 일시 불가용 전용(ADR-048)이고, 클라이언트 백오프가 재시도하게 된다.
- 스트리밍 응답을 만들지 마라. 이유: 지연 실측(#96 c) 뒤 결정.
- 프런트엔드·CLI를 고치지 마라. 이유: #96 b의 범위다.
- `services/search.py`·`visibility.py`·마이그레이션을 고치지 마라. 이유: 코어 diff 0.
- 기존 테스트를 깨뜨리지 마라
