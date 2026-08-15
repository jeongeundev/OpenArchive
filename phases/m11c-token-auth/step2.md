# Step 2: bearer-auth

## 배경 — 인증의 입구는 한 곳이고, 권한 게이트는 한 종류뿐이다

`backend/app/api/deps.py`의 `current_user`(:23-33)가 **모든 HTTP 인증의 유일한 입구**다.
쿠키 하나만 읽고 `validate_session`으로 해석한다. 그 위에 게이트가 둘 있다 —
`require_user_id`(:36, 로그인 요구)와 `require_admin`(:43, 계정 관리 권한).

문제는 `require_user_id`가 **읽기와 쓰기를 구분하지 않는다**는 것이다. `documents.py`의
13개 엔드포인트가 전부 이것 하나를 쓰고, 그중 6개가 쓰기다. 그래서 지금 상태로 토큰 인증만
붙이면 `read` scope가 아무 의미도 갖지 못한다.

또 하나: `require_admin`도 `current_user`를 타므로, 방치하면 문서 공급용 토큰으로 **계정
생성·삭제까지** 된다. `docs/PRD.md` IA-2의 최소 권한과 정면으로 어긋난다.

step 1이 자격증명 해석 결과를 `{id, username, is_admin, scope, credential}`로 통일해 두었다.
이 step은 그 dict를 읽는 게이트를 만들고, 실제 라우터에 붙인다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

### 결정 1 — `Authorization: Bearer`가 쿠키보다 우선한다

두 자격증명이 함께 오면 Bearer를 쓴다. 프로그램이 **명시적으로 보낸** 자격증명이 브라우저가
자동으로 붙이는 쿠키보다 의도가 분명하다.

### 결정 2 — Bearer 해석에 실패하면 쿠키로 폴백하지 않는다

`Authorization: Bearer ...` 헤더가 **존재하면** 그 결과만 쓴다. 토큰이 무효면 익명이다.
쿠키가 있어도 쓰지 않는다. 이유는 둘이고, 둘 다 이 phase의 핵심 주장에 직결된다.

1. **폐기가 관측되지 않는다.** 브라우저에 로그인 쿠키가 남은 머신에서 토큰을 폐기하고
   테스트하면 요청이 계속 성공한다 — "독립 폐기가 즉시 무효화된다"(R3)를 확인할 수 없다.
2. **최소 권한이 뚫린다.** `read` 토큰을 보냈는데 쿠키 세션(`read_write`)으로 조용히 승격되면,
   클라이언트가 스스로 권한을 좁힌 것이 무효가 된다.

### 결정 3 — 게이트는 셋이며, 그 이상 만들지 않는다

| 게이트 | 통과 조건 | 실패 |
|---|---|---|
| `require_user_id` (기존) | 주체가 해석됨 | 익명 → 401 |
| `require_write_user_id` (신규) | 주체가 해석됨 + `scope == SCOPE_READ_WRITE` | 익명 401 · `read` 토큰 403 |
| `require_session_user` (신규) | 주체가 해석됨 + `credential == CREDENTIAL_SESSION` | 익명 401 · 토큰 403 |

`read_write` ⊃ `read`의 포함관계는 **읽기 게이트가 scope를 아예 보지 않는 것**으로 표현된다.
포함관계 매핑 테이블이나 권한 계층 클래스를 만들지 마라 (step 1 결정 2).

### 결정 4 — `require_admin`은 세션 전용 위에 얹는다. `admin.py`는 변경 0줄이다

`backend/app/api/admin.py:12`는 이미 라우터 전체에 `dependencies=[Depends(require_admin)]`을
걸어 두었다. `require_admin` **안**에서 세션 전용 검사를 하면 관리 API 전체가 자동으로
닫힌다.

> ⚠️ **`admin.py`에 게이트를 또 붙이지 마라.** 이 phase의 직전 작업(m11-a)에서 이미 인증이
> 걸린 라우터 위에 도달 불가능한 가드를 한 겹 더 얹은 사례가 있었다. 파일을 열어 **현재 어떤
> 의존성이 이미 걸려 있는지 먼저 확인**하고, 중복이면 추가하지 마라.

### 결정 5 — 읽기 엔드포인트는 손대지 않는다

검색·문서 목록·상세·관련 문서·태그 추천·링크·백링크·진단·클러스터·시스템 상태는
`require_user_id` 그대로 둔다. 즉 **`read` 토큰으로 전부 조회할 수 있다.** 이것이 의도다 —
모니터링이 `read` 토큰으로 `/api/system/status`를 폴링할 수 있게 되는 것도 여기서 나온다.

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `backend/app/api/deps.py` — **이 step의 1차 수정 대상.** 파일 전체가 55줄이니 전부 읽어라
- `backend/app/services/auth.py` — step 1이 추가한 `validate_token`·`SCOPE_*`·`CREDENTIAL_*`과
  `validate_session`의 새 반환 형태
- `backend/app/api/documents.py` — **2차 수정 대상.** 13개 엔드포인트 중 어느 것이 쓰기인지
  직접 확인하라 (HTTP 메서드로 짐작하지 말고 함수가 무엇을 하는지 보라)
- `backend/app/api/admin.py` — **읽되 고치지 마라** (결정 4). 라우터 레벨 의존성이 어떻게
  걸려 있는지 확인용
- `backend/app/api/search.py`, `system.py`, `clusters.py`, `diagnostics.py` — 읽기 경로.
  건드리지 않는다는 것을 확인하기 위해 훑어라
- `backend/tests/test_deps.py` — 게이트 단위 테스트의 본보기 (dict를 직접 넘겨 부른다)
- `backend/tests/test_documents_api.py` — 쓰기 엔드포인트 계약 전량. 여기에 scope 전수
  테스트를 추가한다
- `backend/tests/test_auth.py`:139 `test_anonymous_and_regular_users_cannot_manage_accounts` —
  관리 API 경계 테스트가 있는 자리
- `docs/ADR.md` 의 **ADR-028** — 경계를 HTTP 계층에 거는 이유(열람 술어를 바꾸지 않는다).
  이 step도 같은 자리에 경계를 건다

## 작업

### 1) 테스트를 먼저 쓴다

**(a) `backend/tests/test_deps.py`** — 게이트 단위 테스트. dict를 직접 넘긴다.

- `require_write_user_id`: `read_write` dict → username 반환 / `read` dict → 403 /
  `None` → 401
- `require_session_user`: `credential="session"` → dict 반환 / `credential="token"` → 403 /
  `None` → 401
- `require_admin`: 세션 + `is_admin=True` → 통과 / **세션 + `is_admin=False` → 403** /
  **`credential="token"` + `is_admin=True` → 403** / `None` → 401
- 401과 403의 detail 문구가 서로 다르다 (인증 부재와 권한 부족은 다른 상황이다)

**(b) `backend/tests/test_auth_api.py`** — Bearer 해석 (`db_client` 사용).

- 유효한 토큰만으로(쿠키 없이) `/api/auth/me`가 발급자를 반환한다
- **쿠키와 토큰이 서로 다른 사용자일 때 토큰 주체로 해석된다** (결정 1)
- **무효한 Bearer + 유효한 쿠키 → 익명 취급**(401 또는 `authenticated: false`). 쿠키로
  폴백하지 않는다 (결정 2)
- `Authorization` 헤더가 `Bearer` 형식이 아니거나(`Basic ...`) 값이 비면 익명이다

**(c) `backend/tests/test_documents_api.py`** — **쓰기 게이트 전수 검증.**

`read` 토큰으로 쓰기 엔드포인트 **전부**를 호출해 403을 확인한다 — 업로드(`POST /api/documents`),
텍스트 공급(`POST /api/documents/text`), 편집(`PUT /{id}`), 태그(`PUT /{id}/tags`),
삭제(`DELETE /{id}`), 재임베딩(`POST /{id}/reembed`). 하나라도 빠지면 게이트 부착 누락을
잡지 못한다. **파라미터화해서 한 테스트로 묶어도 되지만, 엔드포인트 목록을 코드에서
자동 수집하지는 마라** — 자동 수집은 새 엔드포인트가 목록에 안 잡히는 실패 모드를 그대로
물려받는다.

같은 `read` 토큰으로 **읽기는 200**임을 함께 단언한다 (결정 5). 403이 scope 때문이지 토큰
자체가 거부된 것이 아님을 보이는 대조군이다.

**(d) `backend/tests/test_auth.py`** — 관리 API 경계. 기존 :139 테스트 옆에, **admin 계정이
발급한 `read_write` 토큰으로도 `/api/admin/users`가 403**임을 추가한다.

**이 시점에 실행하면 실패한다. 그게 정상이다.**

### 2) `backend/app/api/deps.py`를 고친다

시그니처만 제시한다. 내부 구현은 재량이다.

```python
BEARER_PREFIX = "Bearer "


async def current_user(
    conn: Connection,
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> dict | None:
    """Bearer 토큰을 우선 해석하고, 없을 때만 쿠키 세션을 본다. 무효면 익명이다."""


async def require_write_user_id(user: Annotated[dict | None, Depends(current_user)]) -> str:
    """쓰기를 요구하는 요청의 인증된 사용자명을 반환한다. read scope 토큰은 거부한다."""


async def require_session_user(user: Annotated[dict | None, Depends(current_user)]) -> dict:
    """토큰으로는 열 수 없는 경계. 로그인 세션으로만 통과한다."""
```

- `require_admin`은 `current_user` 대신 `require_session_user`에 의존하도록 바꾼다.
  **401/403 문구와 상태 코드를 바꾸지 마라** — 기존 테스트가 고정하고 있다.
- Bearer scheme 비교는 대소문자를 구분하지 않는 편이 낫다. 다만 그 이상의 파싱(다중 scheme,
  파라미터)은 만들지 마라.
- `deps.py`에서 scope나 credential 값을 **새로 만들어 채우지 마라.** 두 값은 step 1의 서비스가
  이미 dict에 실어 보낸다. 여기서 기본값을 채우면 서비스가 안 보낸 경우를 조용히 덮어
  버린다 — 그 경우는 버그이므로 드러나야 한다.

### 3) `backend/app/api/documents.py`의 쓰기 6개를 교체한다

`require_user_id` → `require_write_user_id`. **읽기 엔드포인트는 그대로 둔다.** 다른 라우터
파일(`search.py`·`system.py`·`clusters.py`·`diagnostics.py`)은 열지도 마라.

## Acceptance Criteria

```bash
cd backend

# 1) 게이트 단위 테스트와 Bearer 해석
.venv/bin/pytest tests/test_deps.py tests/test_auth_api.py -q
#   → 전부 passed

# 2) 쓰기 게이트 전수 + 관리 경계
.venv/bin/pytest tests/test_documents_api.py tests/test_auth.py -q
#   → 전부 passed

# 3) 읽기 경로가 그대로다 (이 step의 회귀 검출기)
.venv/bin/pytest tests/test_search_api.py tests/test_related_api.py tests/test_system_api.py -q
#   → 전부 passed

# 4) 읽기 전용 라우터를 건드리지 않았다
git diff --stat main -- app/api/search.py app/api/system.py app/api/clusters.py app/api/diagnostics.py app/api/admin.py
#   → 출력이 비어 있어야 한다 (변경 없음)

# 5) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `admin.py`에 게이트를 중복으로 얹지 않았는가? (결정 4 — AC 4번이 이것을 잡는다)
   - 열람 술어(`services/visibility.py`)와 검색 SQL을 건드리지 않았는가? 경계는 HTTP
     계층에만 있다 (ADR-028)
   - MCP 서버(`backend/mcp_server/server.py`)가 변경 0줄인가? MCP는 HTTP를 타지 않으므로
     이 step의 영향을 받지 않아야 한다
   - `deps.py`에 인증 방식별 정책 분기(`if credential == ...`로 scope를 정하는 코드)가
     생기지 않았는가? (step 1 결정 1)
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 5에서 일괄 처리).
4. 결과에 따라 `phases/m11c-token-auth/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **HTTP 메서드로 scope를 자동 판정하는 미들웨어를 만들지 마라** (GET이면 read, 나머지는
  write). 이유: 라우터의 의도와 어긋난다 — 읽기 연산이 POST인 엔드포인트가 이미 있고
  (`POST /api/search`), 미들웨어는 새 엔드포인트가 생겼을 때 조용히 잘못된 기본값을 준다.
  게이트는 각 라우터에 명시적으로 붙는다.
- **토큰 관리 엔드포인트를 만들지 마라.** 이유: step 3의 작업이다. 여기서는 게이트만
  만든다.
- **`require_user_id`의 동작을 바꾸지 마라.** 이유: 읽기 경로 전체와 기존 테스트가 그 계약에
  걸려 있다. 새 요구는 새 게이트로 표현한다.
- **읽기 엔드포인트에 scope 검사를 넣지 마라.** 이유: 결정 5. `read` 토큰의 존재 이유가
  사라진다.
- **`X-User-Id` 같은 식별자 헤더를 되살리지 마라.** 이유: ADR-028이 제거한 경로다. 주체는
  서버가 발급·보관한 자격증명의 검증으로만 해석된다 (PRD §6 R4).
- **프론트엔드를 고치지 마라.** 이유: 쿠키 세션의 동작이 바뀌지 않았고, 토큰 관리 UI는
  이 phase의 명시적 non-goal이다.
- **기존 테스트를 깨뜨리지 마라.**
