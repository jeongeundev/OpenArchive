# Step 5: share-auth

Bearer 공유 토큰을 공유 주체로 해석하고, **허용 목록의 읽기 경로만** 공유 주체에게 연다. #97의 REST 시나리오 검증을 여기서 고정한다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 「공유 (2026-10-02, #97 c)」 결정 3·5(주체 값 `share:<uuid>`, 허용 목록), ADR-034
- `backend/openarchive/api/deps.py` — `current_user`, `require_user_id`, `require_write_user_id`, `require_session_user`, `require_admin`
- `backend/openarchive/services/auth.py` — `validate_token`(지금은 `JOIN users`라 공유 토큰은 인증 실패), `validate_session`
- `backend/openarchive/services/visibility.py` — `share_principal`
- `backend/openarchive/services/shares.py`, `backend/openarchive/api/shares.py` — step 3·4
- 라우터 전부: `api/documents.py`, `api/search.py`, `api/clusters.py`, `api/diagnostics.py`, `api/system.py`, `api/groups.py`(`principals_router`), `api/auth.py`(`/me`)
- `backend/tests/test_token_access.py`, `backend/tests/test_deps.py`, `backend/tests/conftest.py`

## 작업

### 1) 테스트 먼저 — 새 `backend/tests/test_share_access.py`(+ 필요하면 `test_deps.py`)

공유·토큰은 step 4의 API로 만든다(소유자 세션 → `POST /api/shares`, `PUT …/documents/{id}`, `POST …/tokens`).

1. **시나리오(#97 검증 항목, REST)**: 문서 100건(조직 공개·제한 섞음, 소유자 둘), 소유자 A의 공유 S에 A의 문서 5건(조직 공개·제한 둘 다 포함), 관계·위키링크가 5건 안팎을 잇게 둔다(임베딩·관계 잡 처리는 conftest 헬퍼 `process_all_embedding_jobs` 등을 쓴다). S 토큰(`Authorization: Bearer …`)으로:
   - `POST /api/search` 결과 문서 id ⊆ 5건, `GET /api/documents` 정확히 5건, `GET /api/documents/progress` 합 5.
   - 5건 안 문서의 `GET /api/documents/{id}`·`/links`·`/backlinks`·`/related`·`/versions/1`·`/file`(원본이 있는 문서) → 200, 응답 어디에도 5건 밖 문서 id가 없다.
   - 5건 밖 문서(조직 공개 포함)의 같은 경로 → 404.
   - `GET /api/clusters`·`GET /api/diagnostics`의 문서 id·개수가 5건 범위 안이다.
   - **픽스처 확인 단언**: 같은 데이터에서 사용자 세션은 5건 밖 관계·링크를 실제로 본다(안 그러면 위 단언이 공허하게 참).
2. **허용 목록 밖은 403**: S 토큰으로 `GET /api/principals`, `GET /api/system/status`, `GET /api/auth/me`, `GET /api/documents/{5건 안 id}/access`, `GET …/tag-suggestions`, 모든 쓰기(`POST /api/documents/text`, `PUT /api/documents/{id}/tags`, `DELETE /api/documents/{id}` 등), `/api/shares`·`/api/auth/tokens`·`/api/admin/*` → 403(쓰기·관리 경로에서 DB 변화 없음).
3. 폐기한 공유 토큰 / 지운 공유의 토큰 → 401(허용 경로에서). 소유자 계정이 지워지면(소유 문서가 없는 소유자로 구성) 토큰도 무효.
4. 사용자 토큰·세션의 기존 동작은 변하지 않는다(기존 테스트 전부 통과).
5. 라우터 전수 단언: `app.routes`를 훑어 공유 주체 허용 의존성(`require_reader`)을 쓰는 경로 집합이 **정확히** ADR-044 결정 5의 허용 목록과 같다. 새 경로가 생기면 이 테스트가 깨져 결정을 강제한다.

### 2) 구현

- `services/auth.py`의 `validate_token`: 공유 토큰이면 `{"kind": "share", "share_id": …, "principal": share_principal(share_id), "scope": "read", "credential": "token", "is_admin": False, "username": None}`을 돌려준다. 사용자 토큰·세션 dict에는 `"kind": "user"`와 `"principal": <사용자명>`을 더한다. 기존 키는 유지한다.
- `api/deps.py`:
  - `require_reader(user) -> str` — 익명 401, 사용자·공유 모두 통과, 주체 값(`principal`)을 반환한다.
  - `require_user_id`, `require_write_user_id`, `require_session_user`(그리고 이를 쓰는 `require_admin`)는 공유 주체를 **403**(문구 예: "공유 토큰으로는 열 수 없는 경로입니다.")으로 거부한다.
- 허용 목록 경로(ADR-044 결정 5: `POST /api/search`, `GET /api/documents`, `GET /api/documents/progress`, `GET /api/documents/{id}`, `GET …/file`, `GET …/files/{file_version}`, `GET …/links`, `GET …/backlinks`, `GET …/related`, `GET …/versions/{version}`, `GET /api/clusters`, `GET /api/diagnostics`)만 `require_user_id` → `require_reader`로 바꾼다. 서비스에는 지금처럼 그 값을 `user_id`로 넘긴다.
- `/api/auth/me`: 공유 주체면 403.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_share_access.py tests/test_token_access.py tests/test_deps.py tests/test_auth_api.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① `require_user_id`의 공유 거부 제거 → 테스트 2(`/api/principals`) 실패 ② 허용 목록 밖 경로 하나(`/tag-suggestions`)를 `require_reader`로 → 테스트 2·5 실패 ③ 공유 주체의 `principal`을 `None`(익명)으로 → 테스트 1(조직 공개 5건 밖이 보임) 실패. 하나라도 통과하면 테스트를 보강한다.
3. 체크리스트: 공유 토큰으로 볼 수 없는 문서가 403이 아니라 404인가(ADR-027)? `/api/system/status`가 공유에 닫혔는가?
4. `phases/m21-shares/index.json`의 step 5를 갱신한다. summary에 허용 목록·dict 형태·mutant 결과를 적는다.

## 금지사항

- 공유 주체에게 쓰기·세션 전용·관리 경로를 열지 마라. 이유: 공유 토큰은 `read`뿐이다(ADR-044 결정 3).
- 허용 목록을 "막을 경로 목록"(거부 목록)으로 구현하지 마라. 이유: 새 경로가 기본으로 열리면 결정 5가 조용히 무너진다.
- MCP 서버를 고치지 마라. 이유: 공유 주체의 접속은 REST만이다(ADR-044 구현 형태 결정 3 개정).
- 라우터에서 열람 판정을 하지 마라 — 술어가 한다.
- 기존 테스트를 깨뜨리지 마라
