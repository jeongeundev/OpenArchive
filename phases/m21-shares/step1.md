# Step 1: shares-schema

공유 주체의 스키마를 `026_shares_tables.sql` 하나로 추가한다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 「공유 (2026-10-02, #97 c)」 절(step 0이 기록), 「구현 형태」의 부여 테이블 결정(종류별 칼럼 + `num_nonnulls` CHECK, 부분 유니크 인덱스 — `UNIQUE NULLS NOT DISTINCT`는 PostgreSQL 15부터라 설치 대상 14에 없어 쓰지 않는다)
- `/docs/ARCHITECTURE.md` — 「DB 스키마」(step 0이 갱신)
- `backend/openarchive/migrations/025_grants_tables.sql` — 같은 형태의 선례(주석 밀도·명명)
- `backend/openarchive/migrations/013_token_tables.sql`, `009_auth_tables.sql`
- `backend/tests/test_tables.py` — 1030행 이후 부여 테이블 테스트(`insert_user`, `insert_group`, `grant_count` 헬퍼)와 `test_tables.py` 상단의 테이블 목록
- `backend/tests/test_migrations.py` — 마이그레이션 러너가 파일을 어떻게 집는지

## 작업

### 1) 테스트 먼저 — `backend/tests/test_tables.py`

1. `shares` 테이블이 있다(상단 테이블 목록 단언에 추가).
2. 같은 소유자 안에서 같은 공유 이름 두 개 → `UniqueViolation`. 다른 소유자는 같은 이름을 쓸 수 있다.
3. 소유자 사용자를 지우면 공유가 사라진다(CASCADE).
4. `document_grants`에 `share_id` 대상 부여를 넣을 수 있다. 대상이 0개·2개 이상(예: user_id+share_id)이면 `CheckViolation` — 기존 `test_a_grant_names_exactly_one_grantee`를 세 칸으로 넓힌다.
5. 같은 (문서, 공유) 부여 두 번 → `UniqueViolation`. 문서나 공유를 지우면 공유 부여가 사라진다.
6. `api_tokens`에 `share_id`만 가진 토큰(scope `read`)을 넣을 수 있다. `user_id`·`share_id` 둘 다 NULL 또는 둘 다 값 → `CheckViolation`. 공유 토큰에 scope `read_write` → `CheckViolation`. 사용자 토큰의 `read_write`는 여전히 된다.
7. 공유를 지우면 그 공유의 토큰이 사라진다.
8. `users.username`이 `share:`로 시작하면 `CheckViolation`(예: `share:x`). `shared`·`myshare:x`처럼 접두사가 아닌 것은 된다.

### 2) 구현 — `backend/openarchive/migrations/026_shares_tables.sql`

- `shares(id uuid PK DEFAULT gen_random_uuid(), owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (owner_user_id, name))`.
- `document_grants`: `share_id uuid REFERENCES shares(id) ON DELETE CASCADE` 추가. 기존 CHECK `document_grants_one_grantee`를 DROP하고 `num_nonnulls(user_id, group_id, share_id) = 1`로 다시 만든다(같은 이름). `uq_document_grants_share ON document_grants (document_id, share_id) WHERE share_id IS NOT NULL`. 술어가 "이 공유의 부여"를 공유 쪽에서 찾을 일은 없지만 공유 화면이 "이 공유의 문서 목록"을 찾으므로 `(share_id) WHERE share_id IS NOT NULL` 인덱스도 둔다.
- `api_tokens`: `user_id` NOT NULL 해제, `share_id uuid REFERENCES shares(id) ON DELETE CASCADE` 추가, `CHECK (num_nonnulls(user_id, share_id) = 1)`, `CHECK (share_id IS NULL OR scope = 'read')`. 이름 있는 제약으로 둔다.
- `users`: `CHECK (username NOT LIKE 'share:%')` — 이름 있는 제약. 주석에 이유(주체 값 `share:<uuid>`와 충돌, ADR-044 「공유」 결정 3)를 적는다.
- 파일 머리 주석에 025처럼 무엇을·왜를 적는다. 공유 부여도 사람이 내린 결정이라 앱이 INSERT한다는 점(025와 같은 근거)을 적는다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_tables.py tests/test_migrations.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① 공유 토큰 scope CHECK 삭제 → 테스트 6 실패 ② username CHECK 삭제 → 테스트 8 실패 ③ `num_nonnulls(user_id, group_id, share_id)`를 `>= 1`로 → 테스트 4 실패. 하나라도 통과하면 테스트를 보강한다.
3. 기존 테스트 중 `api_tokens.user_id NOT NULL`에 기댄 것이 있으면 의미를 확인하고 새 CHECK로 같은 것을 단언하게 고친다(약화 금지).
4. `phases/m21-shares/index.json`의 step 1을 갱신한다. summary에 제약 이름과 mutant 결과를 적는다.

## 금지사항

- 기존 마이그레이션 파일(001~025)을 고치지 마라. 이유: 이미 적용된 DB(VM·PyPI 사용자)에 다시 돌지 않는다.
- 서비스·라우터·술어를 고치지 마라. 이유: step 2~5의 범위다.
- 다형 칼럼(`principal_kind`, `principal_id`)을 쓰지 마라. 이유: FK를 걸 수 없어 고아 부여가 남는다(ADR-044 구현 형태).
- 임시 테이블을 쓰지 마라(CLAUDE.md).
- 기존 테스트를 깨뜨리지 마라
