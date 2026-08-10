# Step 1: auth-service

## 배경 — 비즈니스 로직은 서비스에 둔다

`CLAUDE.md`가 못박았다 — 백엔드 비즈니스 로직은 `backend/app/services/`에 두고,
**API 라우터와 MCP 서버는 이를 재사용만 한다.** 인증도 예외가 아니다.

이 step은 라우터 없이 **해시와 세션의 규칙만** 만든다. step 2가 그 위에 HTTP를 얹는다.

**새 의존성이 0이다** — `hashlib.scrypt`와 `secrets`는 표준 라이브러리다. SBOM이 안 바뀐다는
것이 #37이 이 방식을 고른 이유 중 하나다.

## 읽어야 할 파일

- `backend/app/services/documents.py` — 서비스 계층의 예외 정의·시그니처 스타일.
  **같은 방식으로 쓴다**
- `backend/migrations/009_auth_tables.sql` — step 0이 만든 스키마
- `backend/app/config.py` — 설정을 읽는 방식. 세션 수명이 여기 들어간다

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_auth.py`

- **같은 비밀번호가 매번 다른 해시를 낳는가** (salt가 들어갔는가)
- **틀린 비밀번호가 거부되는가**
- **평문이 저장되지 않는가** — 해시 문자열에 평문이 부분 문자열로 없다
- **세션 토큰이 매번 다른가**
- **만료된 세션이 거부되는가** — 만료 시각을 과거로 넣고 확인한다
- **로그아웃하면 그 토큰이 즉시 무효인가**
- **없는 토큰·빈 토큰이 거부되는가**
- **사용자를 지우면 세션도 사라지는가**

실제 컨테이너에서 돈다 (`CLAUDE.md` CRITICAL).

### 2) `backend/app/services/auth.py`

담을 것:

| 기능 | 방식 |
|---|---|
| 비밀번호 해시 | `hashlib.scrypt` — salt를 함께 저장한다 |
| 비밀번호 검증 | **상수 시간 비교**(`hmac.compare_digest`). `==`를 쓰지 마라 |
| 세션 발급 | `secrets.token_urlsafe(32)` |
| 세션 검증 | 토큰 → 사용자. 만료 확인 포함 |
| 로그아웃 | 세션 행 삭제 |

- `scrypt`의 비용 파라미터(`n`·`r`·`p`)를 **모듈 상수**로 두고, 값 옆에 근거를 한 줄 적는다
- **세션 수명**도 상수 또는 설정. 값을 고른 이유를 적는다
- 예외는 서비스 계층 예외로 정의한다 — HTTP 상태 코드를 서비스가 알면 안 된다

> **인증 실패의 사유를 밖으로 흘리지 마라.** *"없는 사용자"*와 *"비밀번호 틀림"*을 가르면
> 사용자명 존재 여부가 샌다. **한 종류의 실패**로 돌려준다.

## Acceptance Criteria

```bash
cd backend

# 1) 테스트가 있고 통과하는가
test -f tests/test_auth.py
python -m pytest tests/test_auth.py -q

# 2) 검증 항목이 실제로 있는가
grep -c "def test_" tests/test_auth.py      # 8 이상
grep -n "expires" tests/test_auth.py

# 3) 표준 라이브러리만 쓰는가 — 새 의존성이 없어야 한다
git diff pyproject.toml | grep -E "^\+" | grep -viE "^\+\+\+"
#   → 출력이 없어야 한다
grep -nE "^import |^from " app/services/auth.py

# 4) 상수 시간 비교를 쓰는가
grep -n "compare_digest" app/services/auth.py

# 5) 라우터를 만들지 않았는가 — 출력이 없어야 한다
git diff --name-only | grep -E "app/api/|app/main.py"

# 6) 실패 사유가 갈리지 않는가 — 눈으로 확인할 것
grep -nE "없는 사용자|존재하지 않는 사용자|user not found" app/services/auth.py
#   → 사용자에게 나가는 메시지에 이런 구분이 없어야 한다

# 7) 기존 테스트 전부 통과
python -m pytest -q

# 8) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - **테스트가 먼저 쓰였는가?**
   - **`==`로 해시를 비교하지 않았는가?** 타이밍 공격 표면이다
   - **새 의존성이 0인가?** 하나라도 늘면 SBOM 제출 서류가 바뀐다
   - **실패 사유가 하나로 뭉쳐 있는가?**
   - **서비스가 HTTP를 모르는가?** 상태 코드·`HTTPException`이 있으면 계층이 섞인 것이다
3. 결과에 따라 `phases/m8-auth-diagnostics/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **인증 라이브러리(passlib·bcrypt·python-jose 등)를 추가하지 마라.** 이유: 새 의존성 0이
  이 방식을 고른 근거 중 하나다 (#37)
- **JWT를 쓰지 마라.** 이유: #37이 서버 측 세션을 골랐다 — **로그아웃이 진짜로 되어야** 한다.
  JWT는 만료 전 무효화에 별도 저장소가 필요해 세션 테이블과 같은 비용이 든다
- **서비스에 `HTTPException`이나 상태 코드를 넣지 마라.** 이유: `CLAUDE.md` 계층 규칙
- **인증 실패 사유를 갈라 반환하지 마라.** 이유: 사용자명 존재 여부가 샌다
- **라우터·`deps.py`를 고치지 마라.** 이유: step 2다
