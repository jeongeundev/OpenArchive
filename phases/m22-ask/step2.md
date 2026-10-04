# Step 2: answer-service

`services/answer.py` — 검색 결과에서 근거를 길이 예산 안에서 조립하고, 근거마다 기준 버전·현재 버전·개정 여부를 붙여 답변을 만든다.

## 읽어야 할 파일

- `/docs/ADR.md` — ADR-043(「구현 형태 (2026-10-04, #96 a)」 포함), ADR-015 결정 1~2, ADR-027(볼 수 없는 문서는 존재하지 않는다), ADR-017·037(텍스트 버전)
- `/docs/ARCHITECTURE.md` — 「검색 데이터 흐름」(⑤ 직전 텍스트 버전을 `revision`으로 더함), 「근거 기반 답변 (POST /api/ask)」(step 0)
- `backend/openarchive/services/search.py` — `search_documents`, `SearchHit`, `SearchPassage`, `MAX_K`, `apply_vector_search_settings`. **읽기만 한다**
- `backend/openarchive/services/visibility.py` — `VISIBLE_TO_USER`(바인딩 이름 `%(user)s`, 별칭 `d`)
- `backend/openarchive/answers/`(step 1) — `AnswerProvider`, `AnswerUnavailable`, `FakeAnswerProvider`
- `backend/tests/test_search.py` — 문서를 넣고 임베딩하는 픽스처 사용법(`insert_test_document`, `process_all_embedding_jobs`, `migrated_db`), 특히 430~445행의 `revision` 히트를 만드는 방법
- `backend/tests/conftest.py`

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

### 1) 테스트 먼저 — `backend/tests/test_answer.py`

실제 `pgvector/pgvector:pg17` 컨테이너(마이그레이션 적용, `FakeProvider` 임베딩) 위에서. 답변 프로바이더는 `FakeAnswerProvider`와, 테스트 안에서 정의한 작은 클래스(받은 `system`·`prompt`를 기록하는 것, `AnswerUnavailable`을 던지는 것, 호출되면 실패하는 것)를 쓴다. DB를 가짜로 바꾸지 않는다.

1. **인용에 버전이 붙는다** — 문서 하나를 넣고 질문하면 `status == "answered"`, `sources[0]`의 `document_id`·`title`·`chunk_index`·`based_on_version == current_version == 1`·`revised is False`, 답에 `[1]`이 있고 `sources[0].cited is True`.
2. **개정된 문서는 개정 표시** — 같은 문서를 v2로 고친 뒤(앱 경로 또는 `UPDATE documents SET content=...` + 워커 처리) 옛 버전 대목이 `revision` 히트로 근거에 들어오면 그 근거는 `based_on_version == 1`, `current_version == 2`, `revised is True`. 프롬프트의 그 근거 머리에 이전 버전임이 드러난다(예: `v1 기준 · 현재 v2`).
3. **근거 0건이면 "근거 없음"** — 걸리는 문서가 없는 필터(존재하지 않는 태그)로 질문하면 `status == "no_evidence"`, `sources == []`, 프로바이더가 **호출되지 않는다**.
4. **열람 밖 문서는 근거에 절대 안 나온다** — 다른 사용자의 `private` 문서가 질의와 가장 가까워도: `sources`·`hits` 어디에도 그 `document_id`가 없고, 프로바이더가 받은 `prompt`에 그 문서 본문의 고유 문자열이 없다. 익명(`user_id=None`)도 같은 방식으로 확인한다.
5. **미설정이면 검색은 정상·답변만 미설정** — 프로바이더 `None` → `status == "disabled"`, `answer is None`, `hits`는 같은 질의의 `search_documents` 결과와 같은 문서 목록.
6. **모델 실패 시 검색 결과 유지** — `AnswerUnavailable`을 던지는 프로바이더 → `status == "failed"`, `answer is None`, `detail`이 비어 있지 않고, `hits`·`sources`는 그대로. `AnswerUnavailable`이 아닌 예외(`RuntimeError`)는 그대로 올라온다.
7. **길이 예산·중복 제거** — 여러 문서를 넣고 `context_chars`를 작게 주면 근거 본문 길이 합이 예산 이하다. 첫 근거가 예산보다 길면 잘린 하나가 들어간다(빈 목록이 아니다). 같은 본문의 대목은 한 번만 들어간다. 라벨은 1부터 빈틈없이 이어진다.
8. **`cited`** — 답에 나온 라벨만 `True`. 답에 없는 라벨(예: 근거 3개인데 답이 `[2]`만)이면 나머지는 `False`(기록하는 프로바이더가 고정 문자열을 돌려주게 해서 확인).
9. **DB 단계와 생성 단계가 분리된다** — `gather_evidence`가 돌려준 `Evidence`로 `generate_answer`를 부를 때 커넥션이 필요 없다(시그니처에 커넥션이 없고, 커넥션을 닫은 뒤 호출해도 동작).

### 2) 구현 — `backend/openarchive/services/answer.py`

- 계약의 데이터클래스·함수 그대로. `ASK_K = 5`(`1 <= k <= MAX_K`가 아니면 `ValueError` — `search_documents`가 이미 던지므로 그대로 둔다).
- `gather_evidence`: `search_documents`(그대로 호출) → 근거 조립 → 현재 버전 조회 → 프롬프트.
  - 근거 조립: 히트 순서대로, 히트의 `passages`(있으면 그 순서) 아니면 `(hit.chunk_index, hit.content, hit.based_on_version)` 하나. 본문을 `strip()`한 문자열이 이미 들어갔으면 건너뛴다. 남은 예산에 들어가면 넣고, 안 들어가면 건너뛰고 다음을 본다. 근거가 아직 하나도 없는데 첫 대목이 예산보다 길면 예산 길이로 잘라 넣는다.
  - 현재 버전: `SELECT d.id, d.version FROM documents d WHERE d.id = ANY(%(ids)s) AND {VISIBLE_TO_USER}` — `async with conn.transaction():` 안에서(plain BEGIN. `BEGIN READ ONLY` 금지 — OpenProxy가 Replica로 보내 방금 쓴 버전을 놓친다, ADR-010). 결과에 없는 문서(그 사이 열람에서 빠졌거나 지워짐)의 근거는 버리고 라벨을 다시 매긴다.
  - 프롬프트: `system`은 한국어로 — 주어진 근거만 쓴다, 근거에 없으면 "근거 문서에서 찾을 수 없습니다"라고 답한다, 주장마다 `[번호]`로 인용한다, 이전 버전 근거는 현재 문서와 다를 수 있음을 밝힌다. `prompt`는 근거 블록(`[n] 제목 · v{based} 기준[ · 현재 v{current}]` 머리 + 본문) 다음에 질문.
- `generate_answer`: 계약의 상태 순서. 생성은 `await asyncio.to_thread(provider.generate, system, prompt)`. 답에서 `\[(\d+)\]`로 인용 라벨을 모아 `cited`를 채운 새 `AnswerSource`를 만든다(`dataclasses.replace`).
- 모듈 docstring: 고정 파이프라인(ADR-043 결정 1), 함수를 둘로 나눈 이유(생성 동안 커넥션 점유 금지), 답을 저장하지 않음(결정 4).

## Acceptance Criteria

```bash
cd /Users/kje/00_Workspace/01_Coding/project/OpenSQL/backend
docker compose -f ../docker-compose.yml up -d
.venv/bin/pytest tests/test_answer.py -q
.venv/bin/pytest -q -x
.venv/bin/ruff check .
git diff --quiet -- openarchive/services/search.py openarchive/services/visibility.py openarchive/migrations && echo "코어 diff 0"
```

## 검증 절차

1. AC를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① 현재 버전 조회에서 `AND {VISIBLE_TO_USER}` 삭제 — 테스트가 실패하지 않으면, 열람 밖 문서의 근거가 이 경로로 새는 시나리오가 없는 것인지 확인하고 그 사실을 summary에 적는다(검색이 이미 걸러 냄) ② `revised`를 항상 `False` ③ 중복 제거 삭제 ④ 근거 0건에도 프로바이더 호출 ⑤ `AnswerUnavailable` except를 `Exception`으로 넓힘(테스트 6의 `RuntimeError` 단언이 잡아야 한다). ②~⑤는 반드시 테스트가 실패해야 한다.
3. CLAUDE.md CRITICAL 확인: 임시 테이블 없음, `BEGIN READ ONLY` 없음, 앱에서 후처리 열람 필터 없음(열람은 SQL 술어로만), 검색 SQL 무변경.
4. `phases/m22-ask/index.json`의 step 2를 갱신한다. summary에 공개 이름·프롬프트 머리 형식·mutant 결과를 적는다.

## 금지사항

- `services/search.py`·`services/visibility.py`·마이그레이션을 고치지 마라. 이유: 이슈 #96 「코어 diff 0줄」 — 답변은 코어의 소비자다.
- 열람 여부를 파이썬에서 거르지 마라(예: 소유자 비교). 이유: 열람은 술어 하나가 전 경로에 걸리는 구조다(ADR-018·027).
- 답이나 근거를 DB에 저장하지 마라. 이유: ADR-043 결정 4(SSOT).
- `generate_answer`가 커넥션을 받거나 DB를 부르게 하지 마라. 이유: 생성 동안 풀 커넥션 점유 금지(구현 형태 1).
- `api/`·`main.py`를 고치지 마라. 이유: step 3의 범위다.
- 기존 테스트를 깨뜨리지 마라
