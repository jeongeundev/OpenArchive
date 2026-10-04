# Step 1: answer-providers

답변 생성 프로바이더(`fake`·`ollama`)와 설정 5개를 둔다. 기본은 꺼짐(`off` → 프로바이더 없음).

## 읽어야 할 파일

- `/docs/ADR.md` — ADR-043 전체와 그 안의 「구현 형태 (2026-10-04, #96 a)」(step 0이 기록), ADR-003(상용 API 금지)
- `/docs/OPERATIONS.md` — 환경변수 표(step 0이 다섯 줄 추가)
- `backend/openarchive/config.py` — `Settings`의 주석 밀도·`Field(gt=0)` 선례(`job_lease_seconds`)
- `backend/openarchive/embeddings/__init__.py`, `base.py`, `fake.py` — **이 모양을 그대로 따른다**(Protocol 하나, 레지스트리 없음, `get_provider` match 문, 모듈 docstring 밀도)
- `backend/tests/test_embeddings.py`, `backend/tests/test_config.py` — 테스트 관례
- `backend/pyproject.toml` — 기본 의존성 목록(여기에 아무것도 추가하지 않는다)

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

`backend/tests/test_config.py`:
- 다섯 설정의 기본값(`answer_provider == "off"` 등). `ANSWER_PROVIDER=gpt` 같은 값은 검증 오류. `ANSWER_TIMEOUT_SECONDS=0`, `ANSWER_CONTEXT_CHARS=0`은 검증 오류.

`backend/tests/test_answers.py`(새 파일, DB 불필요):
1. `get_answer_provider("off")`는 `None`, `"fake"`는 fake, `"ollama"`는 설정의 url·model·timeout을 가진 Ollama 프로바이더, 그 밖의 이름은 `ValueError`. 이름 없이 부르면 설정을 따른다(환경변수 monkeypatch + `get_settings.cache_clear()` — conftest의 `_clear_settings_cache` 확인).
2. fake는 결정론적이다 — 같은 입력에 같은 출력. 프롬프트에 있는 근거 라벨(`[1]`, `[2]` …, 정규식 `\[(\d+)\]`)을 답에 그대로 인용한다. 라벨이 없으면 인용 없이 "근거 문서에서 찾을 수 없습니다"류 문장을 낸다. 이 성질이 step 2 테스트의 `cited` 판정 근거다.
3. ollama — **실제 Ollama 없이** 테스트 안에서 `http.server.ThreadingHTTPServer`를 `127.0.0.1:0`으로 띄워 검증한다(Mock 라이브러리가 아니라 실제 HTTP 왕복):
   - 요청이 `POST {url}/api/chat`이고 본문이 `{"model": <설정 모델>, "messages": [{"role":"system",...},{"role":"user",...}], "stream": false, "think": false, "options": {"temperature": 0}}`이다.
   - 응답 `{"message": {"content": "  답 [1]  "}}` → `"답 [1]"`(앞뒤 공백 제거).
   - HTTP 500 → `AnswerUnavailable`. 연결 거부(닫힌 포트) → `AnswerUnavailable`. 응답이 타임아웃보다 늦으면(서버가 sleep, 프로바이더 timeout 0.2초) → `AnswerUnavailable`. JSON이 아니거나 `message.content`가 없거나 빈 문자열 → `AnswerUnavailable`.
   - `url` 끝의 `/` 유무와 무관하게 같은 경로로 보낸다.

### 2) 구현

- `backend/openarchive/config.py` — 계약 표의 다섯 필드. 각 필드에 기존 밀도의 주석(무엇·왜). `answer_provider` 주석에 "상용 API 프로바이더는 없다(ADR-003·043)"를.
- `backend/openarchive/answers/__init__.py` — `get_answer_provider`, `__all__`. 모듈 docstring에 "기본 꺼짐이 목적(ADR-043 결정 2)", "예열하지 않는다 — Ollama가 모델 적재를 스스로 관리하고, 꺼져 있을 수 있는 외부 서버를 기동 경로에 넣지 않는다".
- `backend/openarchive/answers/base.py` — `AnswerProvider` Protocol, `AnswerUnavailable`.
- `backend/openarchive/answers/fake.py` — `FakeAnswerProvider`(`name = "fake"`).
- `backend/openarchive/answers/ollama.py` — `OllamaProvider(url: str, model: str, timeout: float)`(`name = "ollama"`). `urllib.request` + `json`만 쓴다. `urllib.error.URLError`·`TimeoutError`·`socket.timeout`·`OSError`·`http.client.HTTPException`·`ValueError`(JSON) 등 호출 실패를 전부 `AnswerUnavailable`로 바꾸되, 원인을 메시지에 남긴다(`raise ... from error`).

## Acceptance Criteria

```bash
cd /Users/kje/00_Workspace/01_Coding/project/OpenSQL/backend
.venv/bin/pytest tests/test_answers.py tests/test_config.py -q
.venv/bin/pytest -q -x
.venv/bin/ruff check .
git diff --quiet -- pyproject.toml && echo "의존성 변경 없음"
```

## 검증 절차

1. AC를 실행한다. (`docker compose up -d`가 안 돼 있으면 먼저 띄운다 — 전체 스위트가 DB를 쓴다.)
2. mutant 확인(직접 바꿔 보고 되돌린다): ① ollama 본문에서 `"think": False` 삭제 ② HTTP 오류를 `AnswerUnavailable`로 바꾸는 except 삭제 ③ `get_answer_provider("off")`가 fake를 반환. 셋 다 테스트가 실패해야 한다. 통과하면 테스트를 보강한다.
3. 아키텍처 체크: 상용 API 클라이언트·새 의존성 없음, `embeddings/`와 같은 모양.
4. `phases/m22-ask/index.json`의 step 1을 갱신한다. summary에 모듈 경로·공개 이름·mutant 결과를 적는다.

## 금지사항

- `pyproject.toml`에 의존성을 추가하지 마라(`httpx`·`requests`·`ollama` 패키지 포함). 이유: 표준 라이브러리로 충분하고 SBOM·라이선스 검증 대상을 늘리지 않는다(ADR-043 구현 형태 7).
- OpenAI 호환·상용 API 프로바이더를 만들지 마라. 이유: 규정 [별표2], ADR-003·043.
- `services/`·`api/`·`main.py`를 고치지 마라. 이유: step 2·3의 범위다.
- Ollama 서버를 실제로 설치·호출하는 테스트를 넣지 마라. 이유: CI 밖(ADR-043 검증 — 실모델은 #96 c).
- `unittest.mock`으로 `urlopen`을 가짜로 바꾸지 마라. 이유: 실제 HTTP 왕복이어야 타임아웃·연결 거부 경로가 검증된다.
- 기존 테스트를 깨뜨리지 마라
