# Step 3: admin-accounts

## 배경 — 계정은 관리자만 만든다

자체 가입이 없으므로 **계정을 만드는 경로가 하나는 있어야 한다.** 실무 ECM은 사내 디렉터리
연동이거나 관리자 발급이고, **초대 코드는 SaaS 협업툴 패턴**이라 #37이 권고를 철회했다.

**관리자 권한은 계정 관리 전용이다.** 남의 `private`을 보지 못한다 — 주면 ADR-027의
*"존재하지 않는 것처럼"*과 정면 충돌하는데, 한정하면 **열람 술어 4곳이 변경 0**이다.

업로드 크기 제한도 여기서 붙인다. 지금은 상한이 없어 **큰 파일 하나로 API 프로세스의
메모리가 통째로 날아간다.**

## 읽어야 할 파일

- `backend/app/services/auth.py` — step 1의 서비스. 사용자 생성도 여기 붙는다
- `backend/app/api/deps.py` — step 2가 만든 신원. 관리자 판정이 그 위에 선다
- `backend/app/api/` 업로드 라우터 — `UploadFile`을 받는 자리
- `backend/app/services/documents.py` — `create_document`. 텍스트 상한이 여기 걸린다
- `backend/migrations/009_auth_tables.sql` — `documents` 본문 CHECK(step 0)

## 작업

### 1) 테스트를 먼저 쓴다

**관리자 경계:**

- 일반 사용자가 계정 생성을 호출하면 거부되는가
- 익명이 호출하면 거부되는가
- **관리자가 남의 `private` 문서를 못 보는가** — 이 단언이 이 step의 핵심이다.
  `tests/test_visibility.py`에 **관리자 시선을 추가**한다
- 관리자가 만든 계정으로 실제 로그인이 되는가

**크기 제한:**

- 상한을 넘는 파일이 **`read()` 전에** 거부되는가
- 상한을 넘는 추출 텍스트가 서비스에서 400으로 거부되는가
- 상한 근처의 정상 파일은 통과하는가

### 2) 계정 관리 API

| 엔드포인트 | 권한 |
|---|---|
| `POST /api/admin/users` | 관리자만 |
| `GET /api/admin/users` | 관리자만 |
| `DELETE /api/admin/users/{id}` | 관리자만 |

- 관리자 판정은 **의존성 하나**로 만들고 라우터가 그것을 쓴다. 라우터마다 `if is_admin`을
  쓰면 하나 빠뜨려도 아무도 모른다
- **사용자를 지울 때 그 사용자의 문서가 어떻게 되는지** 정하고 응답·문서에 적는다.
  `documents.owner_id`는 `text`라 FK가 아니므로 **자동으로 정리되지 않는다** —
  남은 문서가 `private`이면 **아무도 못 보는 유령 문서**가 된다
- 목록 응답에 **비밀번호 해시를 싣지 마라**

### 3) 업로드 크기 제한

| 층 | 상한 | 자리 |
|---|---|---|
| 파일 | **10MB** | `UploadFile.size`를 **`read()` 전에** 확인 |
| 추출 텍스트 | **500KB** | 서비스에서 400. DB CHECK가 최후 방어(step 0) |

- 값은 실측이 아니라 **시연 데이터 최대 문서 90KB의 5배 이상**이라는 근거다.
  **상수로 박고 근거를 옆에 적는다** — 설정 가능하게 만들지 마라
- **`read()` 전에 확인하는 것이 핵심**이다. 읽고 나서 재면 이미 메모리에 올라와 있다

## Acceptance Criteria

```bash
cd backend

# 1) 테스트 통과
python -m pytest tests/test_auth.py tests/test_visibility.py tests/test_documents_api.py -q

# 2) 관리자가 남의 private을 못 보는 단언이 있는가
grep -nE "admin" tests/test_visibility.py

# 3) 관리자 판정이 한 곳에 모였는가 — 라우터마다 흩어져 있으면 안 된다
grep -rn "is_admin" app/api/ | wc -l
#   → 의존성 정의 1곳 + 라우터 참조. 조건문이 라우터마다 반복되면 안 된다

# 4) 크기 제한이 read() 전에 있는가 — 눈으로 확인할 것
grep -n -B3 -A3 "\.size" app/api/*.py

# 5) 응답에 해시가 없는가 — 출력이 없어야 한다
uvicorn app.main:app --port 8904 & sleep 3
curl -s localhost:8904/api/admin/users -b "session=<관리자쿠키>" | grep -i "hash"
kill %1

# 6) 사용자 삭제 시 문서 처리가 정해졌는가 — 코드나 주석에 있어야 한다
grep -rnE "owner_id" app/services/auth.py app/api/

# 7) 기존 테스트 전부 통과
python -m pytest -q

# 8) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - **관리자가 남의 `private`을 볼 수 있게 되지 않았는가?** 이 한 줄이 ADR-027 전체를
     무너뜨린다. 테스트로 고정했는지 확인한다
   - **크기 확인이 `read()` 앞에 있는가?** 뒤에 있으면 제한이 의미 없다
   - **상한을 설정 가능하게 만들지 않았는가?** 요청하지 않은 유연성이다 (`CLAUDE.md`)
   - **사용자 삭제 후 유령 문서가 생기지 않는가?** 정하지 않았으면 지금 정한다
3. 결과에 따라 `phases/m8-auth-diagnostics/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **관리자에게 문서 열람 권한을 주지 마라.** 이유: ADR-027과 정면 충돌한다.
  계정 관리 전용이어야 열람 술어 4곳이 변경 0이다 (#37)
- **자체 가입 엔드포인트를 만들지 마라.** 이유: #37이 기각했다
- **크기 상한을 설정값으로 빼지 마라.** 이유: 요청하지 않은 유연성이다. 상수 + 근거 주석
- **`read()` 후에 크기를 재지 마라.** 이유: 이미 메모리에 올라와 있다
- **응답에 `password_hash`를 싣지 마라.**
- **프론트를 고치지 마라.** 이유: step 4다
