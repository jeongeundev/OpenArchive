# Step 2: auth-api

## 배경 — `X-User-Id`를 남기면 로그인이 장식이 된다

`backend/app/api/deps.py:23`이 헤더 하나를 **검증 0줄로** 신원으로 삼는다. 세션을 붙여 놓고
이 경로를 남기면 **`curl -H "X-User-Id: alice"` 한 줄로 통과**한다. #37이 *"제거한다"*로
못박은 이유다.

**세션은 httponly 쿠키**로 나른다.

- localStorage 토큰은 **XSS 한 방**에 털린다
- 쿠키면 **로그아웃이 진짜로 된다**
- Next.js가 `/api/*`를 rewrite하므로(`next.config.ts`) **same-origin이라 CORS가 불필요**하다
  (#36이 배포에서 확인했다)

> ⚠️ **`Secure` 속성은 HTTPS를 전제한다.** 평문 HTTP에서 `Secure` 쿠키는 아예 전송되지 않는다.
> 로컬 개발(http)에서 꺼지고 배포(https)에서 켜지도록 **설정으로 분기**한다 —
> 상시 배포의 도메인·인증서는 이 phase 밖의 일이다(지도 #24).

## 읽어야 할 파일

- `backend/app/api/deps.py` — **이 파일이 이 step의 중심**이다
- `backend/app/services/auth.py` — step 1이 만든 것. **재사용만 한다**
- `backend/tests/conftest.py` **182행 부근** — #37이 짚은 헬퍼. 테스트 이관의 시작점이다
- `backend/app/api/` 라우터 전부 — `X-User-Id`에 의존하는 자리
- `backend/mcp_server/` — **MCP는 0줄 바뀐다.** 서비스를 직접 import하고 HTTP를 안 타기 때문이다.
  실제로 그런지 확인만 하라
- `scripts/seed_demo.py`와 `backend/tests/test_seed.py` — 이 저장소의 **CLI 스크립트 관례**다.
  `sys.path`에 backend를 넣어 서비스를 직접 import하고, 테스트는 `backend/tests/`에 둔다.
  작업 4)를 **같은 방식으로** 쓴다

## 작업

### 1) 테스트를 먼저 고친다

`conftest.py`의 사용자 헬퍼를 **세션 기반으로 바꾼다**. #37이 센 바로는 **15군데**가 딸린다.

- 로그인 → 쿠키 → 이후 요청에 실려 나가는가
- **`X-User-Id` 헤더만 보내면 익명으로 취급되는가** — 이 단언이 없으면 헤더 경로가
  살아 있어도 아무도 모른다
- 로그아웃 후 같은 쿠키가 거부되는가
- **익명은 `public`만 본다** — `private`은 목록·검색·관련 문서 어디에도 없다

`tests/test_visibility.py`(m7 step 4)는 **시선을 만드는 방식만 바뀌고 단언은 그대로**여야 한다.
단언까지 고쳐야 통과한다면 권한 동작이 바뀐 것이고, 그건 회귀다.

### 2) `deps.py`를 교체한다

- 쿠키에서 토큰을 읽어 `auth` 서비스로 검증한다
- **토큰이 없거나 무효면 익명**(`user_id = None`)이다 — 400으로 막지 마라.
  익명도 `public`을 읽을 수 있어야 한다
- **`X-User-Id`를 읽는 코드를 지운다.** 남기면 `curl` 한 줄로 통과한다

### 3) 로그인·로그아웃 라우터

| 엔드포인트 | 하는 일 |
|---|---|
| `POST /api/auth/login` | 검증 → 세션 발급 → **httponly 쿠키** |
| `POST /api/auth/logout` | 세션 삭제 → 쿠키 만료 |
| `GET /api/auth/me` | 현재 신원. 익명이면 그렇게 답한다 |

- 라우터는 **서비스를 호출만** 한다
- 응답에 **비밀번호 해시나 토큰을 절대 싣지 마라** — 토큰은 쿠키로만 간다

> ⚠️ **`app/api/schemas.py`는 tdd-guard 매핑 구멍이다.** 훅이 대응 테스트를 요구하지 않으므로
> **스스로 테스트를 쓴다** — 응답에 민감한 필드가 없는지 단언한다.

### 4) 초기 관리자 부트스트랩 — `scripts/create_admin.py`

**자체 가입이 없으므로(#37) 첫 관리자를 만들 수단이 없으면 설치 직후 아무도 로그인할 수 없다.**
계정 관리 API(step 3)도 관리자 로그인을 요구하니 닭-달걀이다. 이 스크립트가 그 고리를 끊는다.
로그인 API를 만드는 이 step에 **로그인할 계정을 만드는 수단**이 함께 있어야 AC 8이 성립한다.

```
ADMIN_PASSWORD=... python3 scripts/create_admin.py <username> [--admin]
```

- **테스트를 먼저 쓴다** — `backend/tests/test_create_admin.py`. `test_seed.py`처럼
  스크립트를 import해 검증한다: 계정이 생기는가 · 생긴 계정의 비밀번호가 auth 서비스로
  검증되는가 · **같은 사용자명 두 번째 실행이 거부되는가**
- `app.services.auth`를 **직접 import해 재사용한다.** 해시 로직을 스크립트에 복제하지 마라 —
  두 벌이 되면 파라미터가 갈린다
- 비밀번호는 **환경변수 또는 stdin**으로 받는다
- 이미 같은 사용자명이 있으면 **덮어쓰지 않고** 그 사실을 알리며 0이 아닌 코드로 끝낸다

## Acceptance Criteria

```bash
cd backend

# 1) 테스트 전부 통과 — 15군데 이관이 끝났는지
python -m pytest -q

# 2) 권한 행동 테스트의 단언이 그대로인가 — 시선 생성 방식만 바뀌었어야 한다
git diff tests/test_visibility.py | grep -E "^-" | grep -viE "^---|X-User-Id|headers"
#   → 출력이 거의 없어야 한다. 단언이 지워졌으면 회귀다

# 3) X-User-Id 경로가 사라졌는가 — 구현부만 검사한다
grep -rn "X-User-Id" app/
#   → 출력이 없어야 한다

# 4) 헤더로 통과하지 못하는 것을 테스트가 단언하는가
grep -rn "X-User-Id" tests/ | head
#   → "헤더를 보내도 익명이다"를 확인하는 테스트에서만 나와야 한다

# 5) 쿠키가 httponly인가
grep -nE "httponly|http_only" app/api/*.py

# 6) MCP가 안 바뀌었는가 — 출력이 없어야 한다 (경로는 backend/mcp_server/ 다)
git diff --name-only | grep "mcp_server/"

# 7) 응답에 토큰·해시가 없는가
grep -rnE "password_hash|token" app/api/schemas.py
#   → 응답 모델에 있으면 안 된다

# 8) 실제로 도는가 — 부트스트랩으로 관리자를 만들고 로그인까지 관통한다
#    이 값은 로컬 개발 DB 검증에만 쓰는 임시 비밀번호다. 사람에게 묻지 말고 그대로 진행하라
export TEST_ADMIN_PW="${TEST_ADMIN_PW:-harness-local-check}"
ADMIN_PASSWORD="$TEST_ADMIN_PW" python ../scripts/create_admin.py admin --admin \
  || echo "이미 존재 — 기존 계정을 쓴다"
uvicorn app.main:app --port 8903 & sleep 3
curl -s -c /tmp/oa-session.txt -X POST localhost:8903/api/auth/login -H 'content-type: application/json' \
     -d "{\"username\":\"admin\",\"password\":\"$TEST_ADMIN_PW\"}" -i | grep -i "set-cookie"
#   → Set-Cookie가 나와야 한다. 안 나오면 로그인 자체가 실패한 것이다
curl -s localhost:8903/api/documents -H "X-User-Id: alice" | head -20
#   → 익명 결과여야 한다 (private 없음)
kill %1

# 9) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - **`X-User-Id`로 통과할 수 있는 경로가 하나도 없는가?** AC 8번을 실제로 실행해 확인한다.
     남아 있으면 이 step 전체가 무의미하다
   - **익명이 400으로 막히지 않는가?** 익명도 `public`은 읽어야 한다
   - **`test_visibility.py`의 단언이 살아 있는가?**
   - **MCP가 정말 0줄인가?** 바뀌었다면 서비스를 우회해 HTTP를 타고 있다는 뜻이다
   - **쿠키가 httponly인가?** 아니면 XSS 한 방이다
3. 결과에 따라 `phases/m8-auth-diagnostics/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`X-User-Id` 경로를 "호환성"으로 남기지 마라.** 이유: 남기면 로그인이 장식이 된다.
  `curl -H` 한 줄로 통과한다 (#37)
- **토큰을 localStorage나 응답 본문으로 내보내지 마라.** 이유: XSS 한 방이고
  로그아웃이 진짜로 되지 않는다
- **익명 요청을 400·401로 막지 마라.** 이유: 익명도 `public`을 읽는 것이 이 제품의 모델이다
- **CORS 설정을 추가하지 마라.** 이유: Next.js rewrite로 same-origin이다 (#36 실측)
- **`tests/test_visibility.py`의 단언을 고치지 마라.** 이유: 구현과 무관하게 권한을
  고정해 둔 테스트다. 시선을 만드는 방식만 바꾼다
- **MCP를 고치지 마라.** 이유: 서비스를 직접 import하므로 HTTP 인증과 무관하다.
  고쳐야 한다면 설계가 어긋난 것이다
- **`create_admin.py`에 계정 목록·삭제·비밀번호 변경을 넣지 마라.** 이유: 설치 후 1회용
  부트스트랩이다. 계정 관리 일반은 step 3의 관리자 API이고, 두 곳에 두면 권한 검사가 갈린다
- **비밀번호를 명령행 인자로 받지 마라.** 이유: `ps` 출력에 평문이 그대로 노출된다
- **이미 있는 계정의 비밀번호를 스크립트로 덮어쓰지 마라.** 이유: DB에 닿을 수 있는 누구나
  관리자를 탈취하는 경로가 된다
