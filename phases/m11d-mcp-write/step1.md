# Step 1: mcp-create-tool

## 배경 — 이 step은 코드이자 실측이다

지금까지 MCP 서버는 읽기 전용 3개 도구(`search_documents`·`get_document`·`list_documents`)만
가졌다. 이 step은 네 번째 도구 `create_document`를 추가해, **AI 에이전트가 소비자에서 공급자가
되는 첫 경로**를 만든다. `docs/PRD.md` C4(공급)가 사람(Web UI)·프로그램(REST)에 이어 요구하는
세 번째 주체다.

**그런데 이 step에는 코드 말고 하나가 더 걸려 있다.** `docs/ADR.md` ADR-035 결정 1과
`docs/PRD.md` §4가 이 작업을 미리 지목해 두었다:

> 이 진입점 위에 **별도 코어 변경 없이** MCP 쓰기 도구(M11-d)를 얹을 때 그 기준(판별 기준 3 —
> 확장의 코어 불변성)을 **처음 실측한다.**

즉 이 step의 산출물에는 **`git diff --stat HEAD -- app/ migrations/`의 출력**이 포함된다.
그 값이 0이어야 PRD가 "플랫폼"이라 부르는 근거 하나가 실측으로 뒷받침된다. AC 3번이 이것을
검사하며, **이 phase에서 가장 중요한 검사다.**

직전 step(step 0)이 코어의 `visibility` 계약 구멍을 이미 메웠다. 그것은 인터페이스 추가와
무관한 별건이었고, 그래서 별도 step으로 분리했다. 이 step은 그 위에 **인터페이스만** 얹는다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

### 결정 1 — 주체는 `MCP_USER_ID`가 정하며, 인자로 받지 않는다

ADR-025는 읽기에 대해 이미 이렇게 정했다: *"사용자 ID를 툴 인자로 받으면 MCP 클라이언트가 임의의
사용자를 사칭해 private 문서를 읽을 수 있다."* 쓰기에도 그대로 적용된다. 도구 인자는
**문서 속성만**이다 — `title`·`content`·`content_type`·`tags`·`visibility`.

`owner_id`·`user_id`·`as_user` 같은 인자를 **절대 만들지 마라.** `docs/PRD.md` §6 R4(사칭 불가)
위반이며, REST가 `X-User-Id` 헤더를 제거한 것(ADR-028)과 같은 결정이다.

### 결정 2 — `MCP_USER_ID` 미설정이면 쓰기를 거부한다. 읽기는 지금 그대로다

| `MCP_USER_ID` | 읽기 3개 도구 | `create_document` |
|---|---|---|
| 미설정 | public 문서만 조회 (**지금 동작 그대로**) | ❌ 명확한 오류로 거부 |
| 설정됨 | 그 사용자의 열람 범위 | ✅ 그 사용자가 `owner_id` |

ADR-025의 trusted local context가 갖는 권한은 public **읽기**까지다. 주체 없는 공급은
R5(공급의 귀속 — 삽입 시점에 owner 확정)를 만족할 수 없고, `owner_id`는 `NOT NULL`이라
채울 값도 없다.

**읽기 도구의 동작을 바꾸지 마라.** 미설정 상태의 익명 public 조회는 유지되는 계약이고
기존 테스트가 고정하고 있다.

### 결정 3 — 서비스의 텍스트 진입점을 그대로 부른다

`app.services.documents.create_text_document`를 호출한다. 이름이 비슷한
`create_document`(파일 업로드용, 바이트와 파일명을 받는다)를 **부르지 마라.** MCP 도구 함수의
이름은 `create_document`지만 서비스의 동명 함수와는 다른 것이다.

파일(pdf/docx) 공급은 이 phase의 non-goal이다. 형식은 `txt`·`md`뿐이며, 이는 M11-b가 정한
경계와 같다(ADR-035 결정 4).

### 결정 4 — `visibility` 기본값은 `public`이다. REST와 같다

같은 서비스 함수의 기본값을 인터페이스마다 다르게 두면 "하나의 계약 위에서 공급한다"는 C4의
전제가 깨진다. 대신 **도구 docstring에 기본값이 공개임을 명시**해 에이전트가 알고 고르게 한다.

### 결정 5 — 응답은 기존 `_document_payload`를 재사용한다

`mcp_server/server.py:97`의 `_document_payload`가 서비스의 `id`를 MCP 응답 규약인
`document_id`로 바꾼다. 이미 있는 것을 쓰면 `document_id`와 `embedding_status`가 자동으로
포함되고, 읽기 도구와 필드 이름이 어긋나지 않는다. 새 직렬화 함수를 만들지 마라.

`embedding_status`가 응답에 실리는 것이 중요하다 — 에이전트가 **파이프라인이 자동 기동됐음**을
확인하고 `get_document`로 폴링할 수 있어야 한다.

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `backend/mcp_server/server.py` — **이 step의 유일한 주 수정 대상.** 148줄이니 전부 읽어라.
  특히 도구 등록 방식(`:137-139` — 함수를 정의한 뒤 `mcp.tool()(fn)`으로 등록한다.
  데코레이터를 쓰지 않는 이유는 테스트가 함수를 직접 부르기 위해서다)과
  `_document_payload`(`:97`), `lifespan`(`:22`)
- `backend/app/services/documents.py` — `create_text_document`(:142)의 시그니처와 예외.
  **읽되 고치지 마라** (이 step의 핵심 제약)
- `backend/app/config.py` — `mcp_user_id`(:32)와 `get_settings()`의 `lru_cache`.
  테스트에서 `monkeypatch.setenv` 뒤 `get_settings.cache_clear()`가 필요한 이유가 여기 있다
- `backend/tests/test_mcp_server.py` — **테스트를 쓸 자리.** 전체를 읽어라. 특히
  `mcp_database` 픽스처(:34), `_seed_documents`(:48),
  `test_registers_exactly_three_evidence_tools`(:71 — **갱신 대상**)
- `backend/tests/conftest.py` — `insert_test_document`(:144),
  `process_all_embedding_jobs`(:169, async), `run_embedding_worker`(:179, sync).
  MCP 테스트는 async이므로 어느 쪽을 써야 하는지 직접 확인하라
- `backend/tests/test_documents_api.py`:56 `test_text_ingest_matches_upload_pipeline_derivatives`
  — **파생 대조 테스트의 본보기.** MCP 경로에도 같은 형태를 쓴다
- `backend/tests/test_architecture.py` — 구조 규칙 테스트. `DERIVED_TABLE_INSERT`(:7)와
  `test_application_code_does_not_insert_into_derived_tables`(:37)의 형태를 따른다
- `docs/ADR.md` 의 **ADR-025**(MCP 사용자 컨텍스트), **ADR-035**(텍스트 공급 의미론),
  **ADR-015**(정합성 계약 — 파생은 DB가 만든다)
- `docs/PRD.md` §4(판별 기준 3)와 §6(R4·R5·IA-3)

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_mcp_server.py`

**(a) 도구 집합 고정 테스트를 갱신한다** (`:71`)

4개 집합(`search_documents`·`get_document`·`list_documents`·`create_document`)으로 바꾼다.
함수명도 개수에 맞게 고친다. **"정확히 이 집합"이라는 성격은 반드시 보존하라** — 이 테스트의
목적은 도구가 조용히 늘어나는 것을 막는 것이다. `>=`나 부분집합 비교로 바꾸지 마라.

**(b) `MCP_USER_ID` 미설정이면 쓰기가 거부된다**

`monkeypatch.delenv("MCP_USER_ID")` 상태에서 `create_document(...)` 호출이 예외를 낸다.
예외 메시지가 무엇을 해야 하는지 알려주는지 확인한다(환경변수 이름이 들어가야 한다 —
에이전트가 읽고 사람에게 전달할 문구다). **그리고 `documents` 행이 늘지 않았음을 단언한다.**

같은 상태에서 읽기 3개 도구가 여전히 public 문서를 반환하는 것을 대조군으로 단언한다
(결정 2 — 거부가 도구 전체가 아니라 쓰기에만 걸린다는 증거).

**(c) 생성 문서의 `owner_id`가 `MCP_USER_ID`와 일치한다** (R5)

`MCP_USER_ID=alice`로 생성한 뒤 응답과 DB 양쪽에서 확인한다.

**(d) 파생 4종이 자동 기동된다** — 이 phase의 심사 핵심

MCP 도구로 문서를 만들고 임베딩 잡을 처리한 뒤, DB에서 직접 센다:

| 파생 | 확인 |
|---|---|
| `embedding_jobs` | 1건 (트리거가 만들었다) |
| `document_versions` | 1건 (초기 텍스트 버전) |
| `document_chunks` | 1건 이상 |
| `document_links` | 본문에 `[[제목]]`을 넣고 그 target_title이 기록됐는지 |
| `document_edges` | 1건 이상 |

`test_text_ingest_matches_upload_pipeline_derivatives`처럼 **REST 업로드로 만든 문서와
대조**하면 더 강하다. 다만 대칭 비교만으로는 양쪽이 나란히 아무것도 만들지 않아도 통과하므로,
기준점 값을 먼저 못박아라(그 테스트의 `:114-118` 주석이 이유를 설명한다).

**(e) MCP로 만든 private 문서가 다른 주체에게 보이지 않는다**

`MCP_USER_ID=alice`로 `visibility="private"` 문서를 만든 뒤, `MCP_USER_ID`를 다른 값으로
바꾸거나 지우고 `list_documents`·`search_documents`에서 사라지는지 확인한다. 공급 경로가
늘어도 열람 계약이 그대로임을 고정한다 (ADR-027, IA-3).

**(f) 잘못된 `visibility`가 MCP 경로에서도 거부된다**

step 0이 코어에 넣은 가드가 이 인터페이스에서도 작동하는지 확인한다. 문서가 저장되지 않는
것까지 단언한다.

**(g) 지원하지 않는 `content_type`이 거부된다**

`create_text_document`가 이미 던지는 `UnsupportedFileType`이 도구 호출에서 전파되는지.

### 2) 구조 테스트를 추가한다 — `backend/tests/test_architecture.py`

**MCP 서버가 SQL을 직접 실행하지 않는다**는 것을 검사하는 테스트를 추가한다.
`backend/mcp_server/` 아래 Python 파일에 SQL 실행 호출(`.execute(`)이 없어야 한다.

이 테스트가 이 step의 금지사항(코어 복제 금지)을 **실행 가능한 검사로** 바꾼다. 인터페이스가
코어의 검증·INSERT를 자기 안에 베껴 넣으면 반드시 SQL 실행이 따라오기 때문이다. 기존
`test_application_code_does_not_insert_into_derived_tables`(:37)의 형태 — 경로를 훑고 위반
목록을 모아 한 번에 단언 — 를 따르고, 실패 메시지에 위반 파일 경로를 담아라.

현재 `server.py`에는 SQL 실행이 한 줄도 없다. 테스트를 먼저 쓰면 **이 시점에 통과한다**.
그것이 정상이다 — 이 테스트는 회귀 방지 장치이지 실패에서 출발하는 테스트가 아니다.

### 3) `backend/mcp_server/server.py`에 도구를 추가한다

시그니처만 제시한다. 내부 구현은 재량이다.

```python
class MissingUserContext(Exception):
    """MCP_USER_ID가 없어 공급 주체를 확정할 수 없는 경우."""


async def create_document(
    title: str,
    content: str,
    content_type: Literal["txt", "md"] = "md",
    tags: list[str] | None = None,
    visibility: Literal["public", "private"] = "public",
) -> dict:
    """문서 텍스트를 저장하고 임베딩 파이프라인을 기동합니다.

    (docstring이 곧 에이전트가 읽는 도구 설명이다. 한국어로 쓰고, 반드시 담을 것:
     기본 공개범위가 public이라는 사실 · 소유자는 서버 환경이 정하며 인자로 지정할 수 없다는
     사실 · 임베딩은 비동기라 응답의 embedding_status가 pending일 수 있다는 사실)
    """
```

- `Literal` 타입 힌트를 쓰는 이유: FastMCP가 이것을 JSON Schema의 `enum`으로 노출해 에이전트가
  허용 값을 알 수 있다. 값의 최종 권위는 코어의 가드다 — **여기서 값 목록을 다시 검사하는
  `if` 문을 쓰지 마라.**
- 등록은 기존 방식과 같이 `mcp.tool()(create_document)`로 파일 하단에 추가한다.
- DB 접근은 `async with get_pool().connection() as conn:` 패턴 그대로다. psycopg의 컨텍스트
  매니저가 커밋을 처리하므로 **`conn.commit()`을 직접 부르지 마라.**
- 서비스 예외(`UnsupportedFileType`·`EmptyExtractedText`·`ExtractedTextTooLarge`·
  `InvalidVisibility`)를 **잡지 마라.** FastMCP가 도구 예외를 오류 응답으로 만들고, 예외 문구는
  이미 사용자에게 보일 수 있게 쓰여 있다. try/except로 감싸 메시지를 다시 쓰면 코어가 정한
  문구가 인터페이스에서 갈라진다.

## Acceptance Criteria

```bash
cd backend

# 1) 새 도구와 구조 규칙
.venv/bin/pytest tests/test_mcp_server.py tests/test_architecture.py -q
#   → 전부 passed

# 2) 코어가 이 step에서 변경되지 않았다 — 이 phase의 핵심 실측 (PRD §4 판별 기준 3)
git diff --stat HEAD -- app/ migrations/
#   → 출력이 비어 있어야 한다. 한 줄이라도 나오면 실패다.
#     기준이 origin/main이 아니라 HEAD인 이유: step 0이 코어를 이미 고쳤으므로
#     origin/main 기준으로는 두 변경이 섞여 이 실측이 무의미해진다.

# 3) 프론트엔드·예제·스크립트도 이 step의 대상이 아니다
git diff --stat HEAD -- ../frontend/ ../examples/ ../scripts/
#   → 출력이 비어 있어야 한다

# 4) 읽기 경로와 REST 동등성 회귀 없음
.venv/bin/pytest tests/test_search_api.py tests/test_documents_api.py tests/test_related_api.py -q
#   → 전부 passed

# 5) MCP 서버가 기동하고 정상 종료한다 (실측: 약 0.6초, exit 0)
.venv/bin/python -m mcp_server.server < /dev/null; echo "exit=$?"
#   → exit=0

# 6) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. **AC 2번의 출력을 step summary에 그대로 적는다.** 이 phase는 그 수치를 산출물로 요구한다
   (step 2가 ADR과 PRD에 기록한다). 0줄이면 "코어 변경 0줄"이라고 명시하라.
3. 아키텍처 체크리스트를 확인한다:
   - 도구 인자에 주체를 지정하는 것(`owner_id`·`user_id`·`as_user`)이 없는가? (결정 1)
   - `mcp_server/server.py`에 SQL 문자열이나 `.execute(` 호출이 없는가? (2번 테스트가 잡는다)
   - 읽기 3개 도구의 시그니처·동작이 그대로인가?
   - `embedding_jobs`에 애플리케이션이 직접 INSERT하지 않는가? 잡은 트리거가 만든다
     (CLAUDE.md CRITICAL — `test_architecture.py:37`이 잡는다)
   - 도구 집합 고정 테스트가 "정확히 이 4개"를 단언하는가? 부분집합 비교로 약해지지 않았는가?
4. `docs/` 문서는 이 step에서 고치지 않는다 (step 2에서 일괄 처리).
5. 결과에 따라 `phases/m11d-mcp-write/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약 + 코어 변경 실측치"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **코어 변경을 피하려고 `services`의 검증·INSERT를 `mcp_server`에 복제하지 마라.** 이유:
  AC 2번을 통과시키는 잘못된 방법이 정확히 이것이다. 그러면 숫자는 0이 되지만 측정하려던
  성질(계약이 한 곳에 있다)은 오히려 파괴된다. 코어 변경이 **정말로 필요하다고 판단되면**,
  복제하지 말고 `status: "blocked"`로 중단하고 그 이유를 `blocked_reason`에 적어라 — 그것도
  판별 기준 3의 유효한 실측 결과다.
- **`update_document`·`delete_document`·`reembed` 도구를 만들지 마라.** 이유: 이슈 #66의
  명시적 non-goal이다. 최소형은 create 하나이며, 필요가 확인된 뒤 별도로 판단한다.
- **HTTP transport나 원격 MCP를 도입하지 마라.** 이유: stdio 유지가 ADR-025의 트레이드오프
  그대로이고, 프로세스 신뢰 모델이 바뀌면 `MCP_USER_ID` 계약 전체가 무효가 된다.
- **읽기 3개 도구에 인증·권한 검사를 새로 넣지 마라.** 이유: 미설정 상태의 public 조회는
  유지되는 계약이다 (결정 2). MCP는 HTTP를 타지 않으므로 M11-c의 토큰과도 무관하다.
- **`app/` 아래를 고치지 마라.** 이유: AC 2번이 이 step의 존재 이유다. 라우터에 MCP용
  엔드포인트를 추가하는 것도 여기 포함된다.
- **`get_settings()` 결과를 모듈 수준 변수에 캐시하지 마라.** 이유: 기존 코드가 매 호출마다
  `get_settings().mcp_user_id`를 읽는 것은 `lru_cache`와 `cache_clear()`로 테스트가 환경을
  바꾸기 때문이다. 모듈 로드 시점에 굳히면 테스트가 통째로 무력해진다.
- **`prompts`나 `resources` 같은 다른 MCP 기능을 추가하지 마라.** 이유: 이 step의 범위는
  도구 하나다.
- **기존 테스트를 깨뜨리지 마라.**
