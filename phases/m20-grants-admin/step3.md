# Step 3: groups-api

관리자 그룹 API와 부여 대상 목록 API를 새 라우터 `api/groups.py`에 둔다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 「관리 경로 (2026-10-01, #97 b)」, ADR-034 결정 6(세션 전용), ADR-040
- `backend/openarchive/services/grants.py` — step 1 산출물
- `backend/openarchive/api/admin.py` — `/api/admin/users`의 형식(`dependencies=[Depends(require_admin)]`, 예외 → HTTP 매핑, 한국어 detail)
- `backend/openarchive/api/deps.py` — `require_admin`(세션 전용 + 관리자), `require_user_id`
- `backend/openarchive/api/schemas.py` — `UserSummary`·`CreateUserRequest`
- `backend/openarchive/main.py` — 라우터 등록
- `backend/tests/test_auth_api.py`·`backend/tests/test_token_access.py` — 관리자 API·토큰 경계 테스트 형식, `login_as` 헬퍼(`conftest.py`)

## 작업

### 1) 테스트 먼저 — `backend/tests/test_groups_api.py`

1. 관리자 세션: `POST /api/admin/groups {"name": "인사팀"}` → 201 `{id, name, created_at, members: []}`. 같은 이름 → 409. 빈 이름 → 422.
2. `GET /api/admin/groups` → 목록(구성원 사용자명 포함).
3. `PUT /api/admin/groups/{id}/members/{username}` → 204, 두 번 해도 204. 없는 그룹·사용자 → 404.
4. `DELETE /api/admin/groups/{id}/members/{username}` → 204(구성원이 아니어도 204). 없는 그룹·사용자 → 404.
5. `DELETE /api/admin/groups/{id}` → 204, 없는 그룹 → 404.
6. 경계: 비관리자 세션 → 403, 익명 → 401, **관리자의 API 토큰(Bearer, read_write)** → 403. 다섯 엔드포인트 전부(파라미터화).
7. `GET /api/principals`: 로그인 사용자(세션·토큰 모두) → 200 `{"users": [...], "groups": [...]}`. 익명 → 401.
8. 그룹 구성원 추가 API 직후 그 그룹에 부여된 private 문서가 `GET /api/documents/{id}`로 보이고, 제거 API 직후 404가 된다(부여는 테스트에서 `services.grants.insert_grants`로 만든다).

### 2) 구현

- `backend/openarchive/api/groups.py`: 라우터 둘.
  - `router = APIRouter(prefix="/api/admin/groups", tags=["admin"], dependencies=[Depends(require_admin)])`
  - `principals_router = APIRouter(prefix="/api/principals", tags=["principals"])`, `GET ""`은 `require_user_id`.
- 스키마(`schemas.py`): `CreateGroupRequest(name: str, min_length=1 — 공백만은 서비스 ValueError → 422)`, `GroupSummary(id, name, created_at, members: list[str])`, `Principals(users: list[str], groups: list[str])`.
- 예외 매핑: `GroupAlreadyExists` → 409 "이미 존재하는 그룹 이름입니다.", `GroupNotFound` → 404 "그룹을 찾을 수 없습니다.", `UserNotFound` → 404 "사용자를 찾을 수 없습니다.", 빈 이름 `ValueError` → 422.
- `main.py`에 두 라우터를 등록한다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_groups_api.py tests/test_auth_api.py tests/test_token_access.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: `router`의 `dependencies`를 `require_user_id`로 바꾸면 테스트 6이 실패하는지 직접 바꿔 보고 되돌린다. 실패하지 않으면 테스트를 고친다.
3. 체크리스트: `/api/admin/*`가 세션 전용인가(CLAUDE.md CRITICAL, ADR-034)? 라우터가 서비스만 부르는가?
4. `phases/m20-grants-admin/index.json`의 step 3을 갱신한다. summary에 경로·스키마 이름을 적는다.

## 금지사항

- 그룹 관리 엔드포인트를 토큰으로 열지 마라. 이유: CLAUDE.md CRITICAL — `/api/admin/*`는 세션 전용(ADR-034).
- 그룹 이름 변경(`PATCH`) 엔드포인트를 만들지 마라. 이유: 이름이 부여 지정 계약이다.
- `GET /api/principals`에 is_admin·id·생성일 등 이름 외 정보를 싣지 마라. 이유: 대상 선택에 필요한 것은 이름뿐이고, 디렉터리를 넓게 열 이유가 없다.
- `api/documents.py`를 고치지 마라. 이유: step 4의 범위다.
- 기존 테스트를 깨뜨리지 마라
