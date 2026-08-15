# Step 0: token-tables

## 배경 — 비대화형 자격증명이 없어 프로그램은 사람의 비밀번호를 빌려 쓴다

현재 HTTP 인증 수단은 로그인 쿠키 하나뿐이다(`backend/migrations/009_auth_tables.sql`의
`sessions`). 그래서 문서를 공급하는 프로그램·커넥터·CI가 **사람의 username/password로
로그인해 쿠키를 얻는 것 말고는 방법이 없다** — `examples/ingest_text.py`가 그렇게 동작한다.

`docs/PRD.md` §6은 이것을 R3(자격증명은 주체와 분리, 한 주체가 여럿, 각각 독립 폐기)와
IA-2(비대화형 자격증명의 독립 폐기 + 최소 권한)의 미충족으로 적어 두었고, C4를
"비대화형 자격증명 미구현"으로 표시하고 있다.

이 step은 그 자격증명을 담을 테이블 하나를 만든다. 서비스·엔드포인트는 이후 step의 몫이다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

이 phase의 인증 모델은 **위임 토큰**이다. 사람 계정(`users` 행)이 발급하는 API 토큰이며,
별도 서비스 주체(자체 계정)는 만들지 않는다. 따라서 **`users` 테이블은 건드리지 않는다.**

### 결정 1 — 토큰은 SHA-256 해시로만 저장한다

`sessions.token`은 원문을 저장하지만 API 토큰은 그 관례를 따르지 않는다. 세션은 수명이 짧고
브라우저 쿠키에만 있지만, API 토큰은 **CI 설정·스크립트·커넥터 구성에 장기간 박히는
자격증명**이라 노출 창의 크기가 다르다. DB 덤프가 유출됐을 때 원문 토큰은 즉시 사용 가능한
자격증명이 된다.

해시는 **salt 없는 단일 `hashlib.sha256`**이다. 비밀번호처럼 scrypt를 쓰지 않는 이유는 둘이다.

1. 토큰은 `secrets.token_urlsafe(32)`가 만드는 256비트 고엔트로피 값이라 사전 공격·무차별
   대입의 대상이 아니다. 비용 파라미터로 방어할 위협이 존재하지 않는다.
2. salt를 쓰면 행마다 해시가 달라 **인덱스 탐색이 불가능해지고** 매 요청이 전체 스캔 +
   전 행 scrypt 계산이 된다.

### 결정 2 — 만료 컬럼을 두지 않는다

토큰은 장수명이고, 무효화 수단은 **행 삭제(폐기)** 하나다. `expires_at`을 넣지 마라 —
지금 아무도 요구하지 않는 유연성이고(IA-2는 만료가 아니라 독립 폐기와 최소 권한을 요구한다),
만료를 강제하면 커넥터가 어느 날 조용히 죽는다.

### 결정 3 — scope는 `read` / `read_write` 두 값이다

`read_write`가 읽기를 포함한다. 쉼표로 구분된 목록(`"read,write"`)이나 리소스 단위 세분화는
만들지 마라 — `docs/PRD.md` IA-2와 이슈가 세분화를 명시적 non-goal로 못박았고, 파서를
만들어 두면 세분화 압력이 그 자리에서 생긴다. DB가 CHECK 제약으로 두 값을 강제한다.

### 결정 4 — `name`은 장식이 아니라 R3의 전제다

여러 자격증명을 **각각** 폐기하려면 사용자가 어느 것을 지울지 식별할 수 있어야 한다.
`name`(예: `"ci-ingest"`)이 그 식별 수단이다. 반면 `last_used_at`·`revoked_at` 같은
감사 흔적은 넣지 마라 — 감사는 이 phase의 non-goal이고, 폐기는 행 삭제라 남길 상태가 없다.

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `docs/ADR.md` 의 **ADR-028**(최소 로그인 — 한 설치 = 한 조직) — 계정·세션 모델의 근거이자
  이 토큰이 붙을 자리. **ADR-005**(마이그레이션은 번호 붙은 raw SQL + 소형 러너)
- `docs/PRD.md` §6 — R3·R4·IA-1·IA-2. 이 테이블이 무엇을 충족하려는지의 원문
- `backend/migrations/009_auth_tables.sql` — `users`·`sessions`의 형태와 주석 톤. **이 step의
  본보기다**
- `backend/migrations/002_tables.sql` — 컬럼 주석·CHECK 제약을 쓰는 방식
- `backend/tests/test_tables.py` — 특히 `test_auth_table_columns_and_constraints_match_the_account_model`
  (:245-291), `test_deleting_a_user_cascades_to_sessions`(:301-321). **이 step의 테스트는 이
  구조를 그대로 따른다**
- `backend/tests/conftest.py` — `migrated_db` fixture가 실제 마이그레이션 러너를 태운다
- `backend/app/migrations.py` — 러너가 파일을 찾는 규칙(번호 순서·중복 적용 방지)
- `backend/tests/test_migrations.py` — 러너의 계약. 마이그레이션 파일 목록이나 개수를 고정하는
  단언이 있는지 확인하고, 있으면 함께 갱신하라

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_tables.py`에 추가한다(새 파일을 만들지 마라 — 테이블 스키마 테스트는 전부
이 파일에 있다). `conn` fixture(:51)를 그대로 쓴다.

최소 아래를 단언한다:

- `api_tokens`의 **컬럼·데이터 타입·NOT NULL·기본값**이 아래 결정한 모델과 정확히 일치한다
  (`information_schema.columns` 조회 — :245의 기존 테스트와 같은 방식)
- **제약**이 일치한다 — PK, `user_id` FK, `token_hash` UNIQUE
  (`information_schema.table_constraints` 조회 — :270 근처와 같은 방식)
- `scope`에 `'read'`와 `'read_write'`는 들어가고, 그 밖의 값(예: `'write'`, `'admin'`, `''`)은
  **CHECK 위반으로 거부된다**
- 같은 `token_hash`를 두 번 넣으면 UNIQUE 위반이다 (서로 다른 사용자여도 거부되어야 한다)
- **사용자를 삭제하면 그 사용자의 토큰이 함께 사라진다** (:301의 sessions CASCADE 테스트와
  같은 형태). 이것이 "계정 삭제가 곧 전 자격증명 폐기"를 보장한다
- 문서(`documents`) 삭제는 토큰에 아무 영향이 없다 — 두 테이블은 무관하다

**이 시점에 실행하면 `relation "api_tokens" does not exist`로 실패한다. 그게 정상이다.**

### 2) `backend/migrations/013_token_tables.sql`을 만든다

컬럼 구성은 아래로 고정한다. 주석은 `009_auth_tables.sql`의 톤을 따라 **왜 이렇게 정했는지**를
적어라(무엇인지는 코드가 이미 말한다).

```sql
CREATE TABLE api_tokens (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name       text NOT NULL,
  token_hash text NOT NULL UNIQUE,     -- sha256(원문). 원문은 어디에도 저장하지 않는다
  scope      text NOT NULL CHECK (scope IN ('read', 'read_write')),
  created_at timestamptz NOT NULL DEFAULT now()
);
```

- **`user_id`에 별도 인덱스를 만들지 마라.** 이유: 사용자당 토큰은 한 자릿수이고, 목록 조회는
  이 phase에서 유일한 `user_id` 질의다. 인증 경로의 조회는 `token_hash` UNIQUE 인덱스를 탄다.
  근거 없는 인덱스는 쓰기 비용만 늘린다.
- 파일명은 반드시 `013_token_tables.sql`이다. `_tables.sql` 접미사는 tdd-guard 훅이
  `backend/tests/test_tables.py` 대응을 요구하는 규칙과 연결돼 있다(`CLAUDE.md` 개발 프로세스).

## Acceptance Criteria

```bash
cd backend

# 1) 새 스키마 테스트가 통과한다
.venv/bin/pytest tests/test_tables.py -q
#   → 전부 passed

# 2) 마이그레이션 러너가 013을 깨끗하게 적용한다 (기존 12개와의 순서·중복 적용 포함)
.venv/bin/pytest tests/test_migrations.py -q
#   → 전부 passed

# 3) 기존 인증 경로가 그대로다
.venv/bin/pytest tests/test_auth.py tests/test_auth_api.py -q
#   → 전부 passed

# 4) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 스키마 변경이 `backend/migrations/`의 번호 붙은 raw SQL로만 이뤄졌는가? (ORM 마이그레이션
     도구 금지 — ADR-005)
   - `users`·`sessions`·`documents` 등 **기존 테이블을 변경하지 않았는가?**
   - 벡터 컬럼·차원·검색 인덱스를 건드리지 않았는가? (이 step과 무관하다)
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 5에서 일괄 처리).
4. 결과에 따라 `phases/m11c-token-auth/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`users` 테이블을 변경하지 마라.** 이유: 이 phase는 위임 토큰 모델이라 서비스 주체(자체
  계정)를 만들지 않는다. `password_hash NOT NULL`은 그대로 유지되어야 하며, 이는 PRD IA-1의
  "사람임을 전제하는 필드·정책을 새로 추가하지 않는다"와 짝을 이루는 결정이다.
- **`documents.owner_id`(username 텍스트)와 `users.id`(uuid)의 어휘 이중화를 정리하지 마라.**
  이유: 이슈가 이번 범위 밖으로 명시했다. 서비스 주체 도입 시점의 선행 정리 대상이며,
  지금 손대면 열람 술어 네 곳과 전 테스트가 함께 흔들린다.
- **`expires_at`·`revoked_at`·`last_used_at`을 넣지 마라.** 이유: 결정 2·4. 만료는 요구되지
  않았고 폐기는 행 삭제이며 감사는 non-goal이다.
- **서비스 코드나 라우터를 만들지 마라.** 이유: `services/auth.py`는 step 1, `deps.py`와
  엔드포인트는 step 2·3이다. 한 커밋에 섞으면 스키마 변경만 따로 되돌릴 수 없다.
- **기존 마이그레이션 파일(001~012)을 수정하지 마라.** 이유: 이미 적용된 환경(개발 DB·VM)이
  있어 제자리 수정은 반영되지 않는다. 변경은 새 번호 파일로만 한다.
- **기존 테스트를 깨뜨리지 마라.**
