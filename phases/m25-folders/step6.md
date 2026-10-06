# Step 6: folder-api

step 3·4의 서비스를 REST로 연다 — 폴더 라우터 신설, 문서 라우터의 폴더 인자·이동·개별 지정. 라우터·스키마만 다룬다.

## 공통 배경 — m25-folders 설계 결정 (모든 step 같음)

이슈 #187의 폴더 백엔드다(목록 찾기는 m24-doc-finder, 화면은 다음 phase, CLI `import --keep-folders --grant-group`은 그다음 phase). 근거 ADR-054, 스파이크 `notes/folder-acl-spike-20261005.md`(로컬 전용 — 없으면 아래 요약으로 충분하다). 2026-10-06 사용자 결정:

- **D1 범위는 최상위 폴더만 갖는다.** 최상위 폴더는 「조직 공개(public)」 또는 「제한(private) + 사용자·그룹 부여」. 하위 폴더는 항상 「상위 폴더 범위 따름」 — 자기 범위가 없다. 새 최상위 폴더 기본은 조직 공개.
- **D2 폴더 이동(폴더를 다른 폴더 아래로)은 없다.** 문서의 폴더 이동만 있다. 그래서 폴더의 최상위 조상은 만든 뒤 바뀌지 않는다.
- **D3 폴더를 볼 수 있는 사용자는 그 안에 하위 폴더를 만들고 자기 문서를 넣을 수 있다.** 넣은 문서는 기본 「폴더 범위 따름」이다.
- **D4 폴더를 만든 계정은 삭제를 거부한다** — 문서를 소유한 계정 삭제 거부(`services/auth.py` `delete_user`)와 같은 방식. 권한 이전 기능은 없다.
- **D5 볼 수 없는 폴더 안의 문서가 「개별 지정」으로 보이면** 문서는 목록·검색에 나오되 폴더 정보(id·이름·경로)는 어디에도 나오지 않는다. 🔒 같은 자리 표시도 없다(CLAUDE.md: 표시 자체가 존재를 누출한다).
- **D6 폴더 문서 수와 폴더별 목록은 그 폴더에 직접 든 문서만** 센다(하위 폴더 제외). 검색 폴더 필터만 하위 폴더를 포함한다.
- **D7 벡터 검색에 `SET LOCAL hnsw.iterative_scan = strict_order`를 건다.** 스파이크: 열람 범위가 좁은 사용자(문서 5.9%)의 recall@10 0.40 → 1.00, 폴더 필터 0.27 → 0.99. ADR-011 보강 3의 "켜지 않는다"를 뒤집는 결정이다.
- **D8 경계**: 폴더 열람 범위 변경은 **세션 전용**(`require_session_user`), 관리자도 불가 — 폴더를 **만든 사람만**. 폴더 만들기·이름 변경·삭제·문서 폴더 이동은 문서 쓰기와 같은 경계(`require_write_user_id`, 토큰 허용). 이름 변경·삭제는 만든 사람 또는 관리자(볼 수 있는 폴더에 한함). 문서 폴더 이동은 문서 소유자만. MCP는 이번에 바꾸지 않는다.
- 그 밖의 ADR-054 결정: 폴더 범위 변경·그룹 구성원 변경은 **조회 시점 판정**으로 즉시 반영(실효 범위를 저장하지 않는다). 폴더를 만든 사람은 자기 제한 폴더 안의 남의 문서(「폴더 범위 따름」)도 본다. 「개별 지정」 문서는 폴더 범위와 무관하게 문서 자신의 `visibility`·부여로 판정하며, 폴더를 만든 사람에게도 그 판정대로다. 문서 소유자는 언제나 자기 문서를 본다. 외부 공유 주체(`share:<uuid>`)는 폴더를 보지 못하고, 공유에 부여된 문서만 본다(지금과 같음). 삭제는 빈 폴더만("폴더가 비어 있지 않습니다.") — 하위 폴더가 있어도 비어 있지 않다.
- 술어 형태(스파이크 A1): 문서의 폴더에서 **조상으로 거슬러 올라가는 상관 재귀 `EXISTS (WITH RECURSIVE …)`**. 비상관 `IN (서브쿼리)`는 금지 — 3천 청크에서 HNSW를 버리고 generic plan에서 15배 느려졌다. `db.py`의 `prepare_threshold=None`은 유지한다.

### 스키마 (step 0이 만든다 — `030_folders_tables.sql`)

- `folders(id uuid PK, parent_id uuid NULL → folders ON DELETE RESTRICT, name text, created_by text(사용자명, documents.owner_id와 같은 방식), visibility text NULL, created_at, updated_at)`
  - CHECK: 이름은 공백뿐이 아니고 `/`를 포함하지 않는다. `visibility IN ('public','private')`. **최상위만 범위를 갖는다**: `(parent_id IS NULL) = (visibility IS NOT NULL)`.
  - 같은 부모 아래 이름 유일(하위 폴더만). **최상위 이름은 유일하게 하지 않는다** — 남의 제한 폴더 이름과 충돌한다는 오류가 그 폴더의 존재를 누출한다.
- `folder_grants(folder_id → folders ON DELETE CASCADE, user_id → users CASCADE, group_id → groups CASCADE, created_at)` — 대상 정확히 하나(CHECK), 대상별 부분 유니크 인덱스. 외부 공유 부여는 없다.
- `documents.folder_id uuid NULL → folders ON DELETE RESTRICT`, `documents.follows_folder boolean NOT NULL DEFAULT true`. 판정: `folder_id IS NOT NULL AND follows_folder`이면 폴더(최상위) 범위, 아니면 문서 자신의 `visibility`·`document_grants`.

## 서비스 (step 3·4)

- `services/folders.py`: `create_folder`, `list_folders(user_id, is_admin)`, `rename_folder(…, is_admin)`, `delete_folder(…, is_admin)`, `get_folder_access`, `set_folder_access`, 예외 `FolderNotFound`·`FolderNotEmpty`("폴더가 비어 있지 않습니다.")·`FolderNameTaken`·`SubfolderScope`·`NotFolderCreator`
- `services/documents.py`: `create_document(…, folder_id)`, `create_text_document(…, folder_id)`, `move_document`, `set_access(…, follows_folder)`, `get_access`(+`follows_folder`·`folder`·`folder_scope`), `get_document`(+`folder`), `list_documents/count_documents(…, folder_id)`
- 인증 의존성(`api/deps.py`): `require_reader`(읽기), `require_write_user_id`(쓰기, 토큰 허용), `require_session_user`(세션 전용 — 토큰 403·공유 거부), `require_admin`. `current_user`가 돌려주는 dict에 `is_admin`이 있다.

## API 계약

| 메서드·경로 | 인증 | 요청 | 응답·오류 |
|---|---|---|---|
| `GET /api/folders` | 읽기(`require_user_id` — 공유 거부) | — | `Folder[]` (`id, parent_id, name, created_by, document_count, scope{visibility, users, groups}, inherited, can_manage, can_change_access`) |
| `POST /api/folders` | 쓰기(토큰 허용) | `{name, parent_id?}` | 201 `Folder`. 볼 수 없는 부모 404, 같은 이름 409 |
| `PATCH /api/folders/{id}` | 쓰기(토큰 허용) | `{name}` | `Folder`. 못 보면 404, 권한 없음 403, 같은 이름 409 |
| `DELETE /api/folders/{id}` | 쓰기(토큰 허용) | — | 204. 못 보면 404, 권한 없음 403, 비어 있지 않음 409 "폴더가 비어 있지 않습니다." |
| `GET /api/folders/{id}/access` | 읽기(`require_user_id` — 공유 거부) | — | 만든 사람만 `{visibility, users, groups}`, 아니면 403(볼 수 없으면 404) |
| `PUT /api/folders/{id}/access` | **세션 전용** | `{visibility, users, groups}` | 만든 사람만(관리자 포함 그 밖은 403), 하위 폴더 400 |
| `GET /api/documents?folder_id=` · `/count?folder_id=` | 기존(`require_reader`) | — | 직접 든 문서만. 공유 주체는 어떤 폴더 id를 줘도 0건(step 4) |
| `POST /api/documents`(업로드 Form) · `POST /api/documents/text` | 쓰기 | `folder_id` 추가 | 폴더 + 열람 범위 동시 지정 400, 못 보는 폴더 404 |
| `PUT /api/documents/{id}/folder` | 쓰기(토큰 허용) | `{folder_id: uuid | null}` | 소유자만, 못 보는 폴더 404 |
| `PUT /api/documents/{id}/access` | 세션 전용(기존) | `follows_folder?: bool` 추가 | 기존 + 개별 지정 전환 |
| `GET /api/documents/{id}` · `/access` | 기존 | — | `folder`(·`follows_folder`·`folder_scope`) 필드 추가 |
| `POST /api/search` | 기존 | `folder_id?` | step 5에서 이미 더했으면 그대로 |

- 생성 시 부여(`grant_users`·`grant_groups`)를 받는 폴더 API는 **만들지 않는다** — 생성 시 부여는 운영자 CLI(`import --grant-group`, 다음 phase)가 서비스로 직접 한다. REST에서 최상위 폴더 범위는 만든 뒤 세션으로 바꾼다.
- 오류 문구는 서비스 예외 메시지를 그대로 `detail`로 낸다.

## 읽어야 할 파일

- `backend/openarchive/api/documents.py`, `api/search.py`, `api/deps.py`, `api/schemas.py`, `backend/openarchive/main.py`(라우터 등록)
- `backend/openarchive/services/folders.py`, `services/documents.py` — step 3·4
- `backend/tests/test_documents_api.py`, `test_access_api.py` — API 테스트 선례(세션·토큰 헬퍼)

## 작업

### 1) 테스트 먼저 — 새 `backend/tests/test_folders_api.py` + 기존 API 테스트 파일

1. 위 표의 각 행: 정상 응답과 오류 코드·문구.
2. **세션 전용 경계**: API 토큰으로 `PUT /api/folders/{id}/access` → 403. 같은 토큰으로 폴더 만들기·이름 변경·문서 이동은 성공.
3. 관리자 세션: 남의 폴더 이름 변경·삭제 성공, 범위 저장 403. 관리자가 볼 수 없는 폴더는 404.
4. 공유 토큰은 `/api/folders` 경로 전부 403(기존 `reject_share` 규칙). **`tests/test_share_access.py`의 `SHARE_READABLE_ROUTES`는 바꾸지 않는다** — 새 폴더 경로에 `require_reader`를 쓰면 허용 목록 단언이 깨진다(ADR-044 공유 결정 5: 새 경로는 기본적으로 공유를 막는다). 공유 토큰으로 `GET /api/documents?folder_id=<공유 문서가 든 폴더>`가 0건이고, `GET /api/documents/{공유 문서}`의 `folder`가 null이다.
5. 업로드 Form에 `folder_id`와 `visibility=private`를 함께 보내면 400.
6. 상세 응답의 `folder`가 볼 수 없는 폴더에서 null(D5).
7. 감사 연동: 세션으로 폴더 범위를 바꾸면 `folder_access_changed` 행의 `actor`가 그 사용자다(행위자 GUC가 `current_user` 의존성에서 걸린다).

### 2) 구현

- 새 `backend/openarchive/api/folders.py`(prefix `/api/folders`), `main.py`에 등록. 스키마는 `api/schemas.py`에.
- 문서 라우터: 업로드 Form·텍스트 요청에 `folder_id`, `PUT /{id}/folder`, `UpdateAccessRequest.follows_folder`, 목록·건수 쿼리 `folder_id`, 상세·access 응답 필드.
- 고정 경로를 `/{document_id}`보다 먼저 등록하는 기존 규칙을 지킨다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_folders_api.py tests/test_documents_api.py tests/test_access_api.py tests/test_search.py tests/test_search_api.py tests/test_share_access.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다(두 번째 줄은 phase 끝 전 백엔드 전체 확인).
2. mutant 확인: `PUT /api/folders/{id}/access`의 의존성을 `require_write_user_id`로 바꾸면 테스트 2가 실패해야 한다.
   - `GET /api/folders`의 의존성을 `require_reader`로 바꾸면 테스트 4와 `test_share_access.py`가 실패해야 한다.
3. `phases/m25-folders/index.json`의 step 6을 갱신한다.

## 금지사항

- 폴더 열람 범위 변경을 토큰·MCP·CLI(REST)로 열지 마라. 이유: CLAUDE.md CRITICAL — 열람 범위 변경(문서·폴더)은 세션 전용(ADR-034·044).
- 라우터에서 권한·가시성을 판정하지 마라. 이유: 서비스가 판정한다(주체 검증은 서비스, CLAUDE.md).
- MCP 서버(`mcp_server/`)를 고치지 마라. 이유: D8 — 이번 범위가 아니다.
- 기존 테스트를 깨뜨리지 마라
