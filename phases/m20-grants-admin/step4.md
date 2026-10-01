# Step 4: access-api

문서 열람 범위 API(`GET`/`PUT /api/documents/{id}/access`)와 업로드·텍스트 API의 부여 대상 인자를
둔다. 서비스는 step 2에 이미 있다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 「관리 경로 (2026-10-01, #97 b)」(결정 2: 변경은 세션 전용, 생성 시 지정은 토큰 허용), ADR-034 결정 6, ADR-047
- `backend/openarchive/services/documents.py` — step 2의 `get_access`·`set_access`·`GrantsOnPublicDocument`, `create_document`·`create_text_document`의 `grant_users`·`grant_groups`
- `backend/openarchive/services/grants.py` — `UnknownGrantee`
- `backend/openarchive/api/documents.py` — `upload_document`(Form, `tags: list[str] | None` 반복 필드 선례), `create_text_document`, `update_tags`(소유자 쓰기 경로의 예외 매핑 — 403/404)
- `backend/openarchive/api/deps.py` — `require_user_id`, `require_write_user_id`, `require_session_user`
- `backend/openarchive/api/schemas.py` — `CreateTextDocumentRequest`
- 앱 전역 예외 핸들러가 있으면(`main.py`) `DocumentNotFound`·`DocumentAccessDenied`가 어떻게 매핑되는지 확인한다
- `backend/tests/test_documents_api.py`, `backend/tests/test_token_access.py`, `backend/tests/conftest.py`(`upload_document`, `login_as`)

## 작업

### 1) 테스트 먼저 — `backend/tests/test_documents_api.py`(또는 새 `test_access_api.py`)

1. 소유자 세션 `GET /api/documents/{id}/access` → 200 `{visibility, users, groups}`.
2. 소유자 세션 `PUT …/access {"visibility": "private", "users": ["bob"], "groups": ["인사팀"]}` → 200, 응답 = 교체 후 상태. bob이 `GET /api/documents/{id}`로 볼 수 있다.
3. `PUT`에 public + 대상 → 400(서비스 문구 그대로), 모르는 이름 → 400(이름이 detail에 있다), 잘못된 visibility 값 → 422.
4. 권한: 볼 수 있는 비소유자 → 403(GET·PUT), 볼 수 없는 사용자 → 404(GET·PUT), 익명 → 401.
5. **세션 전용**: 소유자의 read_write 토큰으로 `PUT …/access` → 403, DB 변화 없음. 같은 토큰으로 `GET …/access` → 200.
6. 업로드: Form `visibility=private`, `grant_users=bob`(반복 가능), `grant_groups=인사팀` → 201, 부여가 생긴다. **read_write 토큰으로도 된다**(생성 시 지정은 허용).
7. 텍스트 API: JSON `grant_users`·`grant_groups` → 같은 동작. public + 대상 → 400, 모르는 이름 → 400, 둘 다 문서가 생기지 않는다.
8. 멱등키: 같은 키 + 다른 대상 → 기존 재사용 응답 코드(지금 코드가 쓰는 값을 따른다).

### 2) 구현

- `api/documents.py`:
  - `GET /{document_id}/access` — `require_user_id`, 응답 `DocumentAccess`.
  - `PUT /{document_id}/access` — **`require_session_user`**(사용자명은 그 dict의 `username`), 본문 `UpdateAccessRequest`, 응답 `DocumentAccess`.
  - `upload_document`에 `grant_users: Annotated[list[str] | None, Form()] = None`, `grant_groups` 같은 형태.
  - `create_text_document`는 스키마 필드를 서비스로 넘긴다.
- `schemas.py`: `DocumentAccess(visibility: Literal["public","private"], users: list[str], groups: list[str])`, `UpdateAccessRequest(visibility: Literal[...], users: list[str] = [], groups: list[str] = [])`, `CreateTextDocumentRequest`에 `grant_users: list[str] | None = None`, `grant_groups: list[str] | None = None`.
- 예외 매핑: `GrantsOnPublicDocument`·`UnknownGrantee` → 400(문구 그대로). 403/404는 기존 소유자 쓰기 경로와 같게.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_documents_api.py tests/test_token_access.py tests/test_visibility.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① `PUT …/access`의 의존성을 `require_write_user_id`로 → 테스트 5 실패 ② 서비스의 public+대상 검사 삭제 → 테스트 3·7 실패 ③ `set_access`의 소유자 판정을 `ensure_visible`만으로 → 테스트 4 실패. 하나라도 통과하면 테스트를 보강한다.
3. 체크리스트: 볼 수 없는 문서의 `/access`가 404인가(ADR-027)? `DocumentSummary`에 부여 목록이 실리지 않았는가?
4. `phases/m20-grants-admin/index.json`의 step 4를 갱신한다. summary에 경로·인자·mutant 결과를 적는다.

## 금지사항

- `PUT …/access`를 토큰에 열지 마라. 이유: 기존 제한 문서의 열람자를 넓히는 관리 행위다(ADR-044 관리 경로 결정 2).
- 업로드·텍스트 생성의 부여 인자를 세션 전용으로 막지 마라. 이유: 같은 결정 — 생성 시 지정은 쓰기 토큰에 허용한다.
- 라우터에서 소유자·열람 판정을 하지 마라. 이유: CLAUDE.md — 주체 문서 검증은 서비스가 한다.
- MCP 서버를 고치지 마라. 이유: step 5의 범위다.
- 기존 테스트를 깨뜨리지 마라
