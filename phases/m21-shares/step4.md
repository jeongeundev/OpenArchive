# Step 4: shares-api

세션 전용 공유 관리 API(`/api/shares`)를 둔다. 서비스는 step 3에 있다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 「공유 (2026-10-02, #97 c)」의 API 형태 표, ADR-034 결정 6(관리 경로는 세션 전용)
- `backend/openarchive/services/shares.py` — step 3
- `backend/openarchive/api/groups.py` — 같은 형태의 라우터 선례(예외 → HTTP 매핑, 204 응답)
- `backend/openarchive/api/auth.py` — 사용자 토큰 발급·폐기 라우트(`TokenCreated`, 201, 원문 응답)
- `backend/openarchive/api/deps.py` — `require_session_user`
- `backend/openarchive/api/schemas.py`, `backend/openarchive/main.py`(라우터 등록, `DocumentNotFound`→404·`DocumentAccessDenied`→403 전역 핸들러)
- `backend/tests/test_groups_api.py`, `backend/tests/test_access_api.py`, `backend/tests/conftest.py`(`db_client`, `login_as`, `upload_document`)

## 작업

### 1) 테스트 먼저 — 새 `backend/tests/test_shares_api.py`

1. `POST /api/shares {"name": "B사"}` → 201 `{id, name, created_at, documents: [], tokens: []}`. 같은 이름 → 409, 빈 이름 → 422(또는 400 — 기존 `groups` 라우트의 관례를 따른다).
2. `GET /api/shares` → 자기 공유만(다른 사용자의 공유가 섞이지 않는다).
3. `PUT /api/shares/{id}/documents/{document_id}` → 204, 다시 해도 204. `GET`에 그 문서(`id`, `title`)가 보인다. `DELETE` 같은 경로 → 204(멱등).
4. 판정: 남의 공유 id / 없는 id → 404. 안 보이는 문서 → 404. 보이지만 남의 문서 → 403. 형식이 틀린 uuid → 422.
5. `POST /api/shares/{id}/tokens {"name": "연동"}` → 201, `token` 원문과 `scope == "read"`. 이후 `GET /api/shares`의 토큰 목록에는 원문이 없다. `DELETE /api/shares/{id}/tokens/{token_id}` → 204, 다시 → 404.
6. `DELETE /api/shares/{id}` → 204, 다시 → 404.
7. **세션 전용**: 사용자의 `read_write` 토큰으로 위 모든 경로 → 403, DB 변화 없음. 익명 → 401.
8. `GET /api/auth/tokens`(사용자 토큰 목록)에 공유 토큰이 섞이지 않는다.

### 2) 구현

- 새 `backend/openarchive/api/shares.py`: `router = APIRouter(prefix="/api/shares", tags=["shares"])`, 모든 라우트가 `require_session_user`(사용자명은 그 dict의 `username`).
- `schemas.py`: `CreateShareRequest(name: str)`, `ShareDocument(id, title)`, `ShareTokenSummary(id, name, scope, created_at)`, `ShareSummary(id, name, created_at, documents, tokens)`, `CreateShareTokenRequest(name: str)`, 발급 응답(`ShareTokenCreated` — 기존 `TokenCreated`를 재사용할 수 있으면 재사용).
- 예외 매핑: `ShareAlreadyExists`→409, `ShareNotFound`·`TokenNotFound`→404. `DocumentNotFound`·`DocumentAccessDenied`는 전역 핸들러를 따른다.
- `main.py`에 라우터 등록.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_shares_api.py tests/test_auth_api.py tests/test_token_access.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① 한 라우트의 의존성을 `require_write_user_id`로 → 테스트 7 실패 ② `ShareNotFound`를 403으로 매핑 → 테스트 4 실패. 하나라도 통과하면 테스트를 보강한다.
3. `phases/m21-shares/index.json`의 step 4를 갱신한다. summary에 경로·상태 코드·mutant 결과를 적는다.

## 금지사항

- 공유 관리 경로를 토큰에 열지 마라. 이유: ADR-034 결정 6 — 토큰이 토큰(공유 토큰)을 발급하면 폐기 뒤에도 자격증명을 재생할 수 있다.
- 라우터에서 소유자·열람 판정을 하지 마라. 이유: CLAUDE.md — 주체 판정은 서비스가 한다.
- 공유 토큰 해석(Bearer)·허용 목록을 만들지 마라. 이유: step 5의 범위다.
- 기존 테스트를 깨뜨리지 마라
