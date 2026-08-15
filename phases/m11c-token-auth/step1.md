# Step 1: token-service

## 배경 — 자격증명 해석의 결과 모양을 여기서 통일한다

step 0이 `api_tokens` 테이블을 만들었다(`backend/migrations/013_token_tables.sql`).
이 step은 그 테이블을 다루는 서비스 함수를 `backend/app/services/auth.py`에 추가한다.

핵심은 함수 네 개를 만드는 것보다 **`validate_session`과 `validate_token`이 같은 모양의
dict를 반환하게 만드는 것**이다. 그래야 다음 step의 `deps.py`가 "쿠키인가 토큰인가"를 분기하지
않고, HTTP 게이트가 dict의 키 두 개만 보고 판단할 수 있다. 인증 방식은 인터페이스별로 달라도
**전부 같은 종류의 주체로 해석된 뒤 코어에 도달한다**는 것이 `docs/PRD.md` §6의 구조다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

### 결정 1 — 해석 결과 dict는 다섯 키를 갖는다

```
{"id": UUID, "username": str, "is_admin": bool, "scope": str, "credential": str}
```

- 세션(`validate_session`) → `scope="read_write"`, `credential="session"`
- 토큰(`validate_token`) → 저장된 scope, `credential="token"`

**세션에 scope가 없는 것이 아니라, 세션의 scope는 항상 `read_write`다.** 사람이 브라우저로
로그인한 세션에 쓰기 제한을 거는 것은 이 phase의 목적이 아니며, 그렇게 해야 기존 Web UI 경로가
한 줄도 바뀌지 않는다.

**이 두 값을 서비스가 채우는 이유**: 자격증명의 권한 범위는 그 자격증명을 발급·검증한 쪽이
아는 사실이다. `deps.py`에서 채우면 HTTP 계층에 "세션이면 read_write"라는 정책 분기가 생기고,
서비스를 직접 부르는 다른 소비자(MCP·스크립트)는 그 사실을 알 수 없게 된다.

### 결정 2 — scope·credential 문자열은 최소한의 상수와 타입 별칭으로만 고정한다

`backend/app/services/auth.py` 모듈 상단에 이것만 둔다.

```python
SCOPE_READ = "read"
SCOPE_READ_WRITE = "read_write"
TokenScope = Literal["read", "read_write"]

CREDENTIAL_SESSION = "session"
CREDENTIAL_TOKEN = "token"
```

**이 이상을 만들지 마라.** `Enum`/`StrEnum` 클래스, `scopes.py` 같은 별도 모듈, scope 포함관계
매핑 테이블, 권한 레지스트리 — 전부 금지다. 이유: 값이 두 개씩이고 **진실의 원천은 이미
DB의 CHECK 제약**이다(step 0). 여기의 상수는 오타 방지와 타입 검사를 위한 것이지 새로운
권한 체계가 아니다. 포함관계(`read_write` ⊃ `read`)는 다음 step의 게이트 한 줄이 표현한다.

`TokenScope`는 `create_token`의 인자 타입으로만 쓴다. `credential` 값은 반환 dict에만 실리고
어떤 함수도 인자로 받지 않으므로 타입 별칭을 만들지 않는다.

### 결정 3 — 원문 토큰은 발급 반환값에만 존재한다

`create_token`이 만든 원문은 반환 dict에 한 번 실리고, DB에는 `sha256` 결과만 들어간다.
이후 어떤 조회로도 원문을 되찾을 수 없다. 이는 결함이 아니라 결정이다(step 0 결정 1).

### 결정 4 — 폐기는 행 삭제이고, 남의 토큰은 폐기할 수 없다

`revoke_token`은 `token_id`와 `user_id`를 **함께** 조건으로 DELETE한다. 삭제된 행이 없으면
예외를 올린다. `WHERE id = %s`만으로 지우고 소유 검사를 라우터에 맡기지 마라 — 서비스가 자기
계약을 지켜야 한다(`CLAUDE.md`의 "주체 문서의 열람 검증을 라우터의 선행 조회에 맡기지 않는다"와
같은 원칙, ADR-018·027).

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `backend/app/services/auth.py` — **이 step의 수정 대상 전체.** 특히 `AuthenticationFailed`
  (:35-39, 실패 사유를 외부에 노출하지 않는다), `create_session`(:143-157),
  `validate_session`(:160-177), `logout`(:180-183), `UserNotFound`(:27)
- `backend/migrations/013_token_tables.sql` — step 0이 만든 테이블 (컬럼·CHECK·CASCADE)
- `backend/tests/test_auth.py` — **이 step의 테스트가 들어갈 파일.** `conn` fixture(:19-23),
  `_create_user` 헬퍼(:25), 그리고 세션 테스트들(:56-137)이 본보기다. 특히
  `test_logout_revokes_only_the_given_session_not_the_others`(:87)는 이 step의
  "토큰 독립 폐기" 테스트가 따를 구조다
- `docs/PRD.md` §6 — R3(독립 폐기)·R4(사칭 불가)·IA-2(최소 권한). 이 함수들이 충족하려는 것
- `docs/ADR.md` 의 **ADR-028** — `hashlib`·`secrets`만 쓰는 이유(새 의존성 0), 서버 측 세션의
  근거

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_auth.py`에 추가한다. 최소 아래를 단언한다:

- **원문이 DB에 없다** — 토큰을 발급한 뒤 `api_tokens`의 모든 텍스트 컬럼을 조회해 원문
  문자열이 어디에도 없음을 확인한다. `token_hash`가 `hashlib.sha256(원문).hexdigest()`와
  일치한다
- 같은 `name`·같은 사용자로 두 번 발급해도 **원문이 서로 다르다**
- `validate_token`이 발급자와 저장된 scope, `credential="token"`을 돌려준다
  (`read`로 발급하면 `read`가, `read_write`로 발급하면 `read_write`가 나온다)
- **`validate_session`도 같은 다섯 키를 갖고**, `scope="read_write"`·`credential="session"`이다
- 폐기한 토큰으로 `validate_token` → `AuthenticationFailed`
- 모르는 토큰·빈 문자열 → `AuthenticationFailed` (실패 사유가 갈리지 않는다)
- **폐기의 독립성(R3)**: 한 사용자가 토큰 2개와 세션 1개를 가진 상태에서 토큰 하나를 폐기하면
  **그 토큰만** 무효가 되고 다른 토큰과 세션은 계속 유효하다
- **남의 토큰은 폐기되지 않는다**: bob이 alice의 `token_id`로 `revoke_token`을 부르면 예외가
  오르고, alice의 토큰은 **여전히 유효하다**(예외만 확인하고 끝내지 마라 — 실제로 살아 있음을
  `validate_token`으로 확인한다)
- `list_tokens`는 자기 토큰만 돌려주고, 결과에 `token_hash`도 원문도 **없다**
- 계정을 삭제하면 그 사용자의 토큰이 전부 무효가 된다 (CASCADE)

**이 시점에 실행하면 `ImportError`/`AttributeError`로 실패한다. 그게 정상이다.**

### 2) `backend/app/services/auth.py`를 고친다

시그니처만 제시한다. 내부 구현은 재량이다.

```python
class TokenNotFound(Exception):
    """폐기할 토큰이 없거나 요청한 주체의 것이 아니다."""


def hash_token(token: str) -> str:
    """원문 토큰의 sha256 hexdigest. salt를 쓰지 않아 인덱스로 조회된다."""


async def create_token(
    conn: psycopg.AsyncConnection,
    user_id: UUID,
    *,
    name: str,
    scope: TokenScope = SCOPE_READ,
) -> dict:
    """토큰을 발급하고 원문을 포함해 반환한다. DB에는 해시만 남는다.

    반환: {"id", "name", "scope", "created_at", "token"}  — "token"이 원문이다.
    """


async def validate_token(conn: psycopg.AsyncConnection, token: str) -> dict:
    """유효한 토큰의 주체를 반환한다. 실패는 AuthenticationFailed 하나로 표현한다."""


async def list_tokens(conn: psycopg.AsyncConnection, user_id: UUID) -> list[dict]:
    """자기 토큰 목록. 해시도 원문도 포함하지 않는다."""


async def revoke_token(conn: psycopg.AsyncConnection, token_id: UUID, *, user_id: UUID) -> None:
    """자기 토큰 한 건을 삭제한다. 대상이 없으면 TokenNotFound."""
```

- 원문 생성은 `secrets.token_urlsafe(32)` — `create_session`(:150)과 같은 관례다.
- `validate_token`은 `validate_session`(:160)과 같은 형태로 `api_tokens`와 `users`를 JOIN해
  한 번의 쿼리로 해석한다. 실패 사유(없는 토큰/폐기된 토큰)를 구분해 노출하지 마라 —
  `AuthenticationFailed`의 docstring이 그 설계를 이미 적어 두었다.
- `validate_session`에 `scope`·`credential` 두 키를 추가한다. **SQL을 고치기보다 반환 직전에
  상수를 얹는 편이 단순하다** — 두 값은 세션 행의 속성이 아니라 세션이라는 자격증명 종류의
  속성이다.
- 새 의존성을 추가하지 마라. `hashlib`·`secrets`·`psycopg`만으로 끝난다(ADR-028).

## Acceptance Criteria

```bash
cd backend

# 1) 인증 서비스 테스트가 통과한다
.venv/bin/pytest tests/test_auth.py -q
#   → 전부 passed

# 2) 세션 반환 형태 변경이 기존 HTTP 경로를 깨뜨리지 않았다
.venv/bin/pytest tests/test_auth_api.py tests/test_deps.py tests/test_documents_api.py -q
#   → 전부 passed

# 3) 새 의존성이 들어오지 않았다
git diff --stat backend/pyproject.toml
#   → 출력이 비어 있어야 한다 (변경 없음)

# 4) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `app/services/auth.py`에 fastapi/starlette import가 없는가? (서비스는 HTTP를 모른다 —
     `tests/test_architecture.py`가 단언한다)
   - scope·credential을 표현하려고 `Enum` 클래스나 새 모듈을 만들지 않았는가? (결정 2)
   - 비밀번호 해시(scrypt) 경로를 건드리지 않았는가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 5에서 일괄 처리).
4. 결과에 따라 `phases/m11c-token-auth/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`deps.py`·라우터를 고치지 마라.** 이유: Bearer 해석과 게이트는 step 2, 엔드포인트는
  step 3이다. 여기서 함께 하면 "서비스만 바뀌었을 때 기존 HTTP 계약이 그대로인가"를 분리해
  검증할 수 없다.
- **토큰 해시에 scrypt·salt를 쓰지 마라.** 이유: 고엔트로피 토큰에는 방어할 사전 공격이 없고,
  salt는 인증 경로를 전체 스캔 + 전 행 KDF 계산으로 만든다 (step 0 결정 1).
- **`AuthenticationFailed`의 메시지나 기존 예외 이름을 바꾸지 마라.** 이유: `api/auth.py:18`이
  그 문구를 401 detail로 그대로 내보내고 테스트가 고정하고 있다.
- **실패 사유를 구분해 반환하지 마라** (없는 토큰 / 폐기된 토큰 / 만료). 이유: 어떤 토큰
  문자열이 "존재하기는 한다"는 정보가 새면 열거 공격의 실마리가 된다. 기존 설계도 같다.
- **토큰으로 다른 토큰을 발급하는 경로를 만들지 마라.** 이유: 폐기해도 스스로 재생하는
  자격증명 체인이 생긴다. 발급 주체 제한은 step 2·3에서 HTTP 게이트로 강제한다.
- **기존 테스트를 깨뜨리지 마라.**
