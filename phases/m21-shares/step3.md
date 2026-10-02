# Step 3: shares-service

공유 관리 서비스(`services/shares.py`)를 두고, 열람 범위 교체(`set_access`)가 공유 부여를 지우지 않게 한다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 「공유 (2026-10-02, #97 c)」 결정 1·2·4와 API 형태 표, ADR-034(토큰: 원문은 응답에 한 번, DB엔 sha256만)
- `backend/openarchive/migrations/026_shares_tables.sql` — step 1
- `backend/openarchive/services/visibility.py` — step 2의 `share_principal`
- `backend/openarchive/services/grants.py` — 같은 층의 선례(예외 클래스, `dict_row`, 이름 정리)
- `backend/openarchive/services/auth.py` — `create_token`·`hash_token`·`list_tokens`·`revoke_token`·`TokenNotFound`
- `backend/openarchive/services/documents.py` — `_load_for_write`(소유자 판정: 안 보이면 `DocumentNotFound`, 보이지만 남의 것이면 `DocumentAccessDenied`), `set_access`, `_read_access`
- `backend/tests/test_grants.py`, `backend/tests/test_document_access.py` — 테스트 형태의 선례

## 작업

### 1) 테스트 먼저 — 새 `backend/tests/test_shares.py`, 그리고 `test_document_access.py`

`test_shares.py`:
1. `create_share(conn, owner=<사용자명>, name=)` → `{id, name, created_at, documents: [], tokens: []}`. 이름 앞뒤 공백 제거, 빈 이름 `ValueError`. 같은 소유자 같은 이름 → `ShareAlreadyExists`. 다른 소유자는 같은 이름 가능.
2. `list_shares(conn, owner=)` → 자기 공유만, 각 공유에 `documents`(`id`, `title`, 제목순)와 `tokens`(`id`, `name`, `scope`, `created_at` — 해시·원문 없음).
3. `delete_share(conn, share_id, owner=)` — 남의 공유·없는 공유 → `ShareNotFound`(존재를 드러내지 않는다). 지우면 그 공유 부여·토큰이 사라진다.
4. `add_document(conn, share_id, document_id, owner=)`: 자기 문서(조직 공개·제한 **둘 다**) → 부여 생성, 두 번 해도 한 행(멱등). 남의 공유 → `ShareNotFound`. 안 보이는 문서 → `DocumentNotFound`. 보이지만 남의 문서(조직 공개 문서 포함) → `DocumentAccessDenied`. **관리자도 남의 문서는 `DocumentAccessDenied`.**
5. `remove_document(...)` — 같은 판정, 없는 부여 제거도 성공(멱등).
6. `issue_share_token(conn, share_id, owner=, name=)` → `{id, name, scope: "read", created_at, token}`. DB에는 `hash_token(token)`만 있고 행의 `share_id`가 그 공유, `user_id`는 NULL. 남의 공유 → `ShareNotFound`.
7. `revoke_share_token(conn, share_id, token_id, owner=)` — 행 삭제. 남의 공유이거나 그 공유의 토큰이 아니면 `TokenNotFound`/`ShareNotFound`(어느 쪽이든 아무것도 지우지 않는다).
8. 사용자 토큰 목록(`auth.list_tokens`)에 공유 토큰이 섞이지 않는다.

`test_document_access.py`:
9. 공유 부여가 있는 문서에 `set_access`(제한 → 조직 공개, 조직 공개 → 제한+사용자 부여 둘 다)를 해도 **공유 부여가 남는다**. `get_access` 응답의 `users`·`groups`에 공유가 섞이지 않는다.
10. 조직 공개 문서에 공유 부여가 있어도 `set_access(public, users=[], groups=[])`는 400 계열 예외 없이 성공한다(「public + 부여 = 400」은 사용자·그룹 부여에만 적용).

### 2) 구현

- `backend/openarchive/services/shares.py`:
  - 예외 `ShareAlreadyExists`, `ShareNotFound`.
  - 함수: `create_share`, `list_shares`, `delete_share`, `add_document`, `remove_document`, `issue_share_token`, `revoke_share_token`. `owner`는 사용자명(다른 서비스의 `user_id`와 같은 값)이며, 공유 소유는 `owner_user_id`(users.id)로 저장하므로 사용자명을 id로 해석한다.
  - 공유 소유 확인은 `WHERE id = %s AND owner_user_id = …` 한 쿼리로 하고, 없으면 `ShareNotFound`(남의 공유와 없는 공유를 구별하지 않는다).
  - 문서 판정은 `documents._load_for_write`를 재사용한다(열람 술어로 존재 판정 → 소유자 판정). 직접 Python으로 열람 규칙을 다시 쓰지 마라.
  - 토큰 원문은 `secrets.token_urlsafe(32)`, 저장은 `auth.hash_token`. 가능하면 `auth.create_token`과 공통부를 나눠 쓰되, 사용자 토큰의 계약(`create_token(conn, user_id, name=, scope=)`)은 바꾸지 마라.
  - 독스트링에 결정 근거(ADR-044 「공유」)를 짧게 적는다.
- `backend/openarchive/services/documents.py`:
  - `set_access`의 부여 DELETE를 사용자·그룹 부여로 한정한다(`share_id IS NULL`). 주석에 이유(공유 부여는 열람 범위와 별개 축, ADR-044 「공유」 결정 2).
  - `_read_access`가 공유 부여를 섞지 않는지 확인한다(현재 JOIN으로 이미 걸러지면 그대로 둔다).

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_shares.py tests/test_document_access.py tests/test_grants.py tests/test_auth.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① `set_access` DELETE의 `share_id IS NULL` 제거 → 테스트 9 실패 ② `add_document`의 소유자 판정을 열람 판정만으로(`ensure_visible`) → 테스트 4(조직 공개 남의 문서) 실패 ③ 공유 소유 조건(`owner_user_id`) 제거 → 테스트 3·4·6 중 실패. 하나라도 통과하면 테스트를 보강한다.
3. `phases/m21-shares/index.json`의 step 3을 갱신한다. summary에 함수 시그니처·예외·mutant 결과를 적는다.

## 금지사항

- 라우터·인증 의존성(`api/`)을 고치지 마라. 이유: step 4·5의 범위다.
- `document_grants`에 공유 부여를 넣는 경로를 `shares.py` 밖에 만들지 마라. 이유: 공유 부여의 소유자 판정이 한 곳에 있어야 한다.
- 공유 토큰 원문을 DB·로그에 남기지 마라(ADR-034).
- 술어(`visibility.py`)를 고치지 마라. 이유: step 2에서 끝났다.
- 기존 테스트를 깨뜨리지 마라
