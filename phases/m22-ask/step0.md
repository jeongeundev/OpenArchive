# Step 0: answer-docs

#96 a(근거 기반 답변 `ask`의 백엔드)의 구현 형태를 코드보다 먼저 문서에 확정한다. 이 step은 **문서만** 고친다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-043** 전체(「답변 생성을 옵션 레이어로 내장한다」), 형식 선례로 ADR-044의 「구현 형태 (2026-10-01, #97 착수 결정)」 절, ADR-015 결정 1~3, ADR-048(일시 불가용·재시도), ADR-044 「공유」 결정 5(허용 목록)
- `/docs/ARCHITECTURE.md` — 「디렉토리 구조」, 「API 설계」(특히 「일시 불가용 응답」), 「검색 데이터 흐름」, 「임베딩 프로바이더」
- `/docs/OPERATIONS.md` — 환경변수 표(`DATABASE_URL`·`EMBEDDING_PROVIDER` … `MAX_UPLOAD_MB`)
- `/docs/PRD.md` — 「답변 생성」 행(옵션 레이어, ADR-043)
- 이슈 #96 본문은 `gh issue view 96`으로 읽는다(「2026-10-03 실행 범위 보완」 포함)

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

### 1) `docs/ADR.md` — ADR-043

- 상태 줄에 `· 2026-10-04 #96 a 구현 형태 반영`을 덧붙인다(초안 → 결정 1~5는 유지, 모델 확정은 #96 c에서).
- ADR-043 끝(「트레이드오프」 뒤, ADR-044 앞)에 **「구현 형태 (2026-10-04, #96 a)」** 절을 추가한다. 위 계약을 근거와 함께 bullet로 적는다. 각 bullet은 "결정 — 이유" 형태로:
  1. **생성 동안 DB 커넥션을 쥐지 않는다** — 인증·검색·현재 버전 조회를 커넥션 한 번 대여로 끝내고 반납한 뒤 생성한다. 이유: 로컬 7B는 CPU에서 초당 수 토큰이라 생성이 수십 초이고, `Connection` 의존성은 요청 끝까지 커넥션을 쥔다 — 동시 질문 몇 개가 풀(ADR-048·`max_connections`)을 말린다. 그래서 `/api/ask`는 스스로 대여·반납하는 인증 의존성을 쓴다.
  2. **응답은 항상 200과 상태 넷** — `disabled`·`no_evidence`·`answered`·`failed`, 검색 결과는 어느 상태에서든 함께 간다. 이유: 결정 2 "미설정이면 검색은 그대로", 이슈 보완 "모델 실패 시 검색 결과는 계속 제공". 503은 DB 일시 불가용(ADR-048)에만 쓴다 — 모델이 꺼진 것은 DB 장애가 아니다. 근거 0건·꺼짐이면 모델을 부르지 않는다.
  3. **근거 목록 = 모델에 준 대목 전체, `cited`로 실제 인용을 구분** — 각 근거에 문서·청크·`based_on_version`·`current_version`·`revised`. 이유: 무엇을 줬는지와 무엇을 인용했는지가 갈려야 #96 c가 충실성(주장과 인용 대목의 일치, 누락)을 잴 수 있다. 검색이 직전 텍스트 버전을 `via=revision`으로 더하므로(ARCHITECTURE ⑤) "v3 기준, 현재 v4"가 실제로 생긴다.
  4. **현재 버전은 검색 SQL을 고치지 않고 따로 조회한다** — 같은 커넥션에서 `documents.version`을 열람 술어(`VISIBLE_TO_USER`)와 함께. 이유: 코어 diff 0줄. 그 사이 열람에서 빠진 문서의 근거는 버린다.
  5. **근거 조립** — 검색 순위대로 문서의 `passages`(없으면 대표 청크)를 넣고, 같은 본문은 한 번만, 글자 예산(`ANSWER_CONTEXT_CHARS`) 안에서. 첫 근거가 예산보다 길면 잘라서라도 하나는 넣는다. 이유: 이슈 보완 "300자 미리보기가 아니라 본문 후보를 길이 예산 안에서, 중복 문맥 없이, 출처 라벨·근거 버전 보존".
  6. **공유 주체는 막는다(403)** — 이유: 새 경로의 기본(ADR-044 「공유」 결정 5). 공유에 열려면 이 ADR과 허용 목록을 함께 바꾼다.
  7. **Ollama는 표준 라이브러리 HTTP로 부른다** — `/api/chat`, `stream:false`, `think:false`, `temperature 0`. 이유: 의존성·SBOM 증가 0. `think:false`는 Qwen3의 추론 출력을 끈다(지연·형식). 스트리밍은 지연을 잰 뒤(#96 c) 결정한다.
  8. **재시도 미들웨어에 `/api/ask`를 넣는다** — 이유: DB 단계가 생성보다 먼저 끝나 재시도가 생성을 두 번 돌리지 않는다. 승격 중에도 검색과 같은 회복을 받는다.
  9. **설정 5개** — 위 표. `answer_model` 기본값 `qwen3:8b`는 임시이며 #96 c의 실측으로 확정한다.

### 2) `docs/ARCHITECTURE.md`

- 「디렉토리 구조」에 `openarchive/answers/`(답변 생성 프로바이더: fake·ollama)와 `services/answer.py`를 기존 서술 밀도로 추가한다.
- 「API 설계」 아래(「인라인 편집과 낙관적 동시성」 뒤)에 **`### 근거 기반 답변 (POST /api/ask)`** 절: 고정 파이프라인 순서, 커넥션 반납 시점, 상태 넷, 응답 모양(`status·answer·detail·sources·items`), 근거 필드, 공유 403, 재시도. ADR-043 「구현 형태」를 참조로 단다. 다이어그램은 넣지 않는다.

### 3) `docs/OPERATIONS.md`

- 환경변수 표에 다섯 줄 추가(`ANSWER_PROVIDER`·`OLLAMA_URL`·`ANSWER_MODEL`·`ANSWER_TIMEOUT_SECONDS`·`ANSWER_CONTEXT_CHARS`). `ANSWER_PROVIDER` 설명에 "기본 `off` — 검색은 그대로, 답변만 미설정. `ollama`는 로컬 Ollama 서버가 필요(설치 절차는 #96 c), `fake`는 테스트용"을 적는다.

## Acceptance Criteria

```bash
cd /Users/kje/00_Workspace/01_Coding/project/OpenSQL
grep -n "구현 형태 (2026-10-04, #96 a)" docs/ADR.md
grep -n "POST /api/ask" docs/ARCHITECTURE.md
grep -c "ANSWER_PROVIDER\|OLLAMA_URL\|ANSWER_MODEL\|ANSWER_TIMEOUT_SECONDS\|ANSWER_CONTEXT_CHARS" docs/OPERATIONS.md   # 5 이상
git diff --stat -- backend frontend   # 출력 없음
```

## 검증 절차

1. AC를 실행한다.
2. 새 절이 ADR-043 안(ADR-044 제목보다 앞)에 있는지 `grep -n "^### ADR-04[34]\|구현 형태 (2026-10-04" docs/ADR.md`로 확인한다.
3. 쓴 문장에 "정답"·"항상 최신"·"실시간"이 없는지 확인한다.
4. `phases/m22-ask/index.json`의 step 0을 갱신한다.

## 금지사항

- 코드·테스트를 고치지 마라. 이유: step 1~3의 범위다.
- ADR-043의 기존 결정 1~5·기각한 대안·트레이드오프 문장을 고치거나 지우지 마라. 이유: 구현 형태는 덧붙이는 기록이고, 결정 개정이 아니다.
- 모델을 "확정"으로 적지 마라. 이유: 한국어 품질 실측(#96 c) 전이다.
- PRD·ROADMAP·README를 고치지 마라. 이유: 사용자 대상 안내는 UI·CLI(#96 b)·실측(#96 c) 뒤에 쓴다.
