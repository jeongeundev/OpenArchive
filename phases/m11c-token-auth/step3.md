# Step 3: token-endpoints

## 배경 — 발급 경로가 없으면 토큰은 존재하지 않는 기능이다

step 0이 `api_tokens` 테이블을, step 1이 `services/auth.py`의 발급·검증·목록·폐기 함수를,
step 2가 `deps.py`의 Bearer 인증과 게이트 셋을 만들었다. 하지만 **사용자가 토큰을 얻을 방법이
아직 없다** — 지금은 DB에 직접 INSERT하거나 파이썬 셸에서 서비스를 부르는 수밖에 없다.

이 step은 자기 토큰을 발급·조회·폐기하는 엔드포인트 셋을 `backend/app/api/auth.py`에 추가한다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

### 결정 1 — 세 엔드포인트는 전부 세션 전용이다

`POST /api/auth/tokens` · `GET /api/auth/tokens` · `DELETE /api/auth/tokens/{token_id}` 모두
step 2의 `require_session_user`를 단다. **토큰으로는 이 셋 중 어느 것도 열 수 없다.**

이유: 토큰으로 새 토큰을 발급할 수 있으면, 유출된 토큰을 폐기해도 스스로 재생하는 자격증명
체인이 생긴다. 목록·폐기까지 막는 이유는 같은 계열이다 — 유출된 토큰이 그 주체의 다른
자격증명을 열거하거나 지울 수 있으면 안 된다.

### 결정 2 — 원문 토큰은 발급 응답에만 실린다

`POST`의 응답에만 `token` 필드가 있고, `GET` 목록에는 없다. 다시 볼 수 없다는 것이 해시
저장의 귀결이며(step 0 결정 1), 이는 결함이 아니라 결정이다. 응답 모델을 나눠 표현한다 —
목록 모델에 `token: str | None`을 두고 평소엔 `None`을 넣는 식으로 얼버무리지 마라.

### 결정 3 — scope 기본값은 `read`다

`CreateTokenRequest.scope`의 기본값은 `SCOPE_READ`다. 최소 권한이 기본이어야 클라이언트가
필요한 범위를 **의식적으로** 넓히게 된다 (`docs/PRD.md` IA-2).

### 결정 4 — 라우터 파일을 새로 만들지 않는다

`backend/app/api/auth.py`(52줄)에 추가한다. 새 모듈과 `main.py`의 라우터 등록을 늘리기에는
엔드포인트 셋이 작고, 전부 같은 인증 도메인이다.

### 결정 5 — 폐기 대상이 없으면 404다

남의 토큰 ID로 폐기를 시도해도 **403이 아니라 404**다. "그 ID는 존재하지만 네 것이 아니다"를
알려주면 다른 사용자의 토큰 ID 존재 여부가 새어 나간다. 이는 `CLAUDE.md`의 "볼 수 없는 것은
존재하지 않는 것처럼 보인다"(ADR-027)와 같은 원칙이다.

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `backend/app/api/auth.py` — **이 step의 수정 대상.** 52줄 전부 읽어라. 라우터 prefix와
  응답 모델 사용 방식이 본보기다
- `backend/app/api/deps.py` — step 2가 만든 `require_session_user`의 시그니처와 반환 dict
- `backend/app/services/auth.py` — step 1이 만든 `create_token`·`list_tokens`·`revoke_token`·
  `TokenNotFound`·`SCOPE_READ`·`TokenScope`
- `backend/app/api/schemas.py` — 응답 모델 작성 규칙. 특히 `AuthStatus`(:62),
  `CreateUserRequest`(:89), `UserSummary`(:95), 그리고 `Literal`을 쓰는
  `CreateTextDocumentRequest`(:36-41)
- `backend/app/api/admin.py` — 서비스 예외를 HTTP 상태로 옮기는 방식(:22, :36-42)의 본보기
- `backend/tests/test_auth_api.py` — **이 step의 테스트가 들어갈 파일.** `db_client` 사용법과
  기존 로그인·로그아웃 테스트 구조
- `backend/tests/test_schemas.py` — 스키마 단위 테스트가 있는지, 있다면 어떤 것을 검증하는지

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_auth_api.py`에 추가한다. 최소 아래를 단언한다:

- 로그인한 사용자가 토큰을 발급하면 **201**과 함께 `token`(원문)·`id`·`name`·`scope`·
  `created_at`이 온다
- **`GET` 목록에는 `token`도 `token_hash`도 없다** — 응답 JSON 전체를 문자열로 만들어 원문이
  포함되지 않음을 확인한다
- 목록은 **자기 토큰만** 보여준다 (alice가 발급한 토큰이 bob의 목록에 없다)
- `scope`를 생략하면 `read`로 발급된다 (결정 3)
- `scope`에 이상한 값(`"admin"`·`"write"`)을 주면 **422**다 (pydantic이 막는다)
- 발급받은 토큰으로 실제 요청이 통과한다 — 쿠키 없이 `GET /api/auth/me`가 발급자를 반환한다
- 폐기하면 **204**이고, 그 토큰으로는 더 이상 인증되지 않는다
- **남의 토큰 ID로 폐기 시도 → 404**이고, 원 소유자의 토큰은 **여전히 유효하다**
  (상태 코드만 보지 말고 살아 있음을 확인하라)
- 없는 UUID로 폐기 시도 → 404
- **세션 전용 경계**: 유효한 `read_write` 토큰(쿠키 없이)으로 세 엔드포인트를 호출하면
  전부 **403**이다
- 익명(쿠키·토큰 모두 없음)으로 세 엔드포인트를 호출하면 전부 **401**이다

**이 시점에 실행하면 404(라우트 없음)로 실패한다. 그게 정상이다.**

### 2) `backend/app/api/schemas.py`에 모델을 추가한다

```python
class CreateTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: Literal["read", "read_write"] = "read"


class TokenSummary(BaseModel):
    id: UUID
    name: str
    scope: str
    created_at: datetime


class TokenCreated(TokenSummary):
    token: str  # 원문. 발급 응답에만 실리며 이후 어떤 조회로도 다시 볼 수 없다
```

`scope`의 허용값은 `Literal`로 직접 적는다 — `services/auth.py`의 `TokenScope`를 import해
써도 되지만, **새로운 공유 상수 모듈을 만들지는 마라** (step 1 결정 2).

### 3) `backend/app/api/auth.py`에 엔드포인트를 추가한다

```python
@router.post("/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
@router.get("/tokens", response_model=list[TokenSummary])
@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
```

- 셋 다 `user: Annotated[dict, Depends(require_session_user)]`를 받는다. 주체는 그 dict의
  `id`(uuid)에서 온다 — **요청 본문이나 쿼리로 `user_id`를 받지 마라** (PRD §6 R4).
- `TokenNotFound` → 404. `admin.py:41`의 `UserNotFound` 처리와 같은 형태다.
- 삭제 응답은 `Response(status_code=204)` — `documents.py:204`와 같다.

## Acceptance Criteria

```bash
cd backend

# 1) 토큰 엔드포인트 테스트가 통과한다
.venv/bin/pytest tests/test_auth_api.py -q
#   → 전부 passed

# 2) 스키마·게이트·서비스가 함께 성립한다
.venv/bin/pytest tests/test_schemas.py tests/test_deps.py tests/test_auth.py -q
#   → 전부 passed

# 3) 기존 HTTP 표면이 그대로다 (회귀 검출기)
.venv/bin/pytest tests/test_main.py tests/test_documents_api.py -q
#   → 전부 passed

# 4) 라우터 파일이 늘지 않았다
git diff --stat main -- app/api/
#   → auth.py·schemas.py·deps.py·documents.py만 나온다. 새 파일이 없어야 한다

# 5) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 라우터가 서비스를 재사용만 하는가? 토큰 SQL이 라우터에 새로 생기지 않았는가?
     (`CLAUDE.md` — 비즈니스 로직은 `services/`에)
   - 세 엔드포인트 전부에 세션 전용 게이트가 붙었는가? (`GET` 목록을 빠뜨리기 쉽다)
   - 응답 모델에 원문 토큰이 새는 자리가 목록·상세 어디에도 없는가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 5에서 일괄 처리).
4. 결과에 따라 `phases/m11c-token-auth/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **토큰 관리 UI를 만들지 마라** (프론트엔드 화면·컴포넌트·API 클라이언트 함수). 이유: 이슈가
  명시적 non-goal로 적었다. API로 충분하며, 필요하면 후속 이슈가 다룬다.
- **토큰 수정(PATCH/PUT) 엔드포인트를 만들지 마라.** 이유: 요청받지 않았다. scope를 바꾸고
  싶으면 폐기하고 새로 발급하는 것이 자격증명의 올바른 수명주기다.
- **토큰 개수 제한·rate limit·사용 이력을 넣지 마라.** 이유: 요청받지 않은 기능이며 감사는
  non-goal이다 (step 0 결정 4).
- **`GET /api/auth/tokens/{id}` 단건 조회를 만들지 마라.** 이유: 목록에 이미 전부 들어 있고
  단건이 쓰이는 자리가 없다.
- **폐기 실패를 403으로 내지 마라.** 이유: 결정 5 — 남의 토큰 ID의 존재 여부가 새어 나간다.
- **`main.py`에 새 라우터를 등록하지 마라.** 이유: 결정 4에 따라 기존 auth 라우터에 붙으므로
  등록은 이미 돼 있다. 중복 등록은 라우트를 두 번 노출한다.
- **기존 테스트를 깨뜨리지 마라.**
