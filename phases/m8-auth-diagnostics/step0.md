# Step 0: auth-tables

## 배경 — 로그인이 화면 둘보다 먼저다

#32가 m8의 배치를 정하며 근거를 남겼다 — **로그인이 화면 2개보다 먼저여야 재작업이 없다.**
진단 목록과 덩어리 그래프가 열람 범위 위에 서는데, 그 범위를 정하는 것이 로그인이기 때문이다.

[#37](https://github.com/jeongeundev/OpenArchive/issues/37)이 형태를 이미 확정했다.
이 step은 그중 **스키마**를 놓는다.

> **경계는 "한 설치 = 한 조직"이다.** `public` = 이 설치의 로그인 사용자 전체,
> `private` = 나만, **익명은 아무것도 못 본다.** 혼자 쓰는 설치에서는 두 값이 같아져
> 모드 분기 없이 개인 지식 관리소가 된다.

**지금 상태가 왜 문제인가.** `backend/app/api/deps.py:23`이 `X-User-Id` 헤더를 **검증 0줄로**
신원으로 삼는다. 그런데 위협은 헤더 위조만이 아니다 — **기본값이 `public`이고 익명이 읽을 수
있다는 것**이며, 그건 **위조조차 필요 없다.** `PRD.md`의 비범위 결정은 *"공개 URL로
배포한다"*는 전제 없이 내려졌다.

## 읽어야 할 파일

- `backend/migrations/002_tables.sql` — 테이블 정의 스타일·주석 밀도·`content_not_blank`
  CHECK의 표현. **새 CHECK를 같은 패턴으로 쓴다**
- `backend/app/api/deps.py` — 지금 신원이 어떻게 정해지는지. **읽기만 하라. step 2가 바꾼다**
- `backend/tests/test_tables.py` — 스키마 테스트 방식

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_tables.py`에 추가

- `users`·`sessions`가 존재하고 컬럼·제약이 맞는가
- 같은 사용자명이 두 번 들어가지 않는가
- 세션의 사용자 FK가 CASCADE인가 (사용자를 지우면 세션도 사라진다)
- **본문 길이 제한이 실제로 거부하는가** — 상한을 넘는 `content` INSERT가 실패한다

### 2) `backend/migrations/009_auth_tables.sql`

```sql
CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username      text NOT NULL UNIQUE,
  password_hash text NOT NULL,     -- hashlib.scrypt. 평문은 어디에도 남기지 않는다
  is_admin      boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
  token      text PRIMARY KEY,     -- secrets.token_urlsafe(32)
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);
```

**주석에 반드시 남길 것:**

- **`is_admin`은 계정 관리 전용이다.** 관리자가 남의 `private`을 보지 못한다 —
  주면 ADR-027의 *"존재하지 않는 것처럼"*과 정면 충돌하고, 한정하면 **열람 술어 4곳이
  변경 0**이다. 실무에서도 시스템 관리자 ≠ 문서 열람 권한이다
- **자체 가입은 없다.** 계정은 관리자만 만든다 — 실무 ECM은 사내 디렉터리 연동이거나
  관리자 발급이고, **초대 코드는 SaaS 협업툴 패턴**이라 #37이 권고를 철회했다
- **새 의존성이 0이다** — `hashlib.scrypt`·`secrets`는 표준 라이브러리라 SBOM이 안 바뀐다

### 3) 본문 길이 제한을 더한다

`documents.content`에 상한 CHECK를 건다. **`content_not_blank`와 같은 패턴**으로 쓴다.

- 값은 **500KB**. 실측이 아니라 **시연 데이터 최대 문서(90KB)의 5배 이상**이라는 근거로
  정한 값이며, 그 근거를 주석에 적는다
- 파일 크기 제한(10MB)은 서비스 계층이다 — **step 3**에서 한다.
  DB는 추출 텍스트만 안다

> ⚠️ **기존 마이그레이션을 고치지 마라.** 러너의 적용 이력이 이미 있어 재적용되지 않는다
> (ADR-005). 새 번호 파일에서 `ALTER TABLE ... ADD CONSTRAINT`로 더한다.
>
> ⚠️ **seed 데이터가 상한에 걸리는지 먼저 확인하라.** m7의 seed가 넣은 문서 중 500KB를
> 넘는 것이 있으면 마이그레이션이 실패한다. 확인하고, 걸리면 값이 아니라 **쪼개기 단위**를
> 다시 본다.

## Acceptance Criteria

```bash
cd backend

# 1) 테스트가 있고 통과하는가
python -m pytest tests/test_tables.py -q

# 2) 마이그레이션이 깨끗한 DB에 전량 적용되는가
docker compose -f ../docker-compose.yml down -v && docker compose -f ../docker-compose.yml up -d && sleep 5
python -m pytest tests/test_migrations.py -q

# 3) 스키마 확인
psql "$DATABASE_URL" -c "\d users"
psql "$DATABASE_URL" -c "\d sessions"

# 4) 길이 제한이 실제로 도는가
psql "$DATABASE_URL" -c "SELECT conname FROM pg_constraint WHERE conrelid='documents'::regclass" | grep -i length

# 5) seed가 여전히 들어가는가 — 상한에 걸리지 않는지
python3 ../scripts/seed_demo.py --reset && python3 ../scripts/seed_demo.py

# 6) 기존 마이그레이션을 안 고쳤는가 — 출력이 없어야 한다
git diff --name-only | grep -E "migrations/00[1-8]_"

# 7) 코드를 안 고쳤는가 — 출력이 없어야 한다
git diff --name-only | grep -E "^backend/app/|^frontend/"

# 8) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - **테스트가 먼저 쓰였는가?**
   - **`is_admin`의 범위가 주석에 한정돼 있는가?** "관리자는 다 본다"로 읽히면
     m8의 나머지 step이 그렇게 구현한다
   - **seed가 길이 제한을 통과하는가?** 걸리면 값을 올리지 말고 쪼개기 단위를 본다
   - **평문 비밀번호가 들어갈 컬럼이 없는가?**
   - **기존 마이그레이션을 안 고쳤는가?** (ADR-005)
3. 결과에 따라 `phases/m8-auth-diagnostics/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`workspace_id`나 테넌시 컬럼을 만들지 마라.** 이유: #37이 근거 둘로 기각했다 —
  요구사항에 테넌시가 한 번도 없고, 매우 선택적인 필터라 `MAX_K * 배수 < EF_SEARCH`
  계산이 전제하지 않은 recall 손실을 만든다
- **`is_admin`에 문서 열람 권한을 부여하는 설계를 넣지 마라.** 이유: ADR-027과 정면 충돌하고,
  한정하면 열람 술어 4곳이 변경 0이다
- **자체 가입용 컬럼(초대 코드·이메일 인증 등)을 만들지 마라.** 이유: #37이 권고를 철회했다
- **외부 인증 라이브러리를 추가하지 마라.** 이유: `hashlib.scrypt`로 **새 의존성 0**이고
  SBOM이 안 바뀐다
- **기존 마이그레이션을 수정하지 마라.** 이유: ADR-005
- **`deps.py`나 라우터를 고치지 마라.** 이유: step 2다
