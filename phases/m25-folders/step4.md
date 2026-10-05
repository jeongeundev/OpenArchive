# Step 4: document-folder

문서 서비스에 폴더를 잇는다 — 업로드·텍스트 생성 때 폴더 지정, 문서의 폴더 이동, 「폴더 범위 따름 / 개별 지정」 전환, 상세·열람 범위 조회의 폴더 정보, 목록의 폴더 필터. 그리고 폴더를 만든 계정의 삭제를 거부한다.

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

## 명세서 시험항목 중 이 step이 받치는 것 (문구가 구현 계약)

- [Pre-condition] 폴더 「인사」가 있음 → 업로드 화면에서 폴더 「인사」를 고르고 올리면 그 문서가 「인사」 폴더에 들어감
- [Pre-condition] 「RFP」가 「제한 · 사업팀」 → kim이 「RFP」를 고른 채 그대로 올린 문서는 사업팀이 아닌 사용자(lee)의 목록·검색에 나타나지 않음
- 트리에서 폴더를 누르면 그 폴더에 든 문서만 목록에 표시됨 / 「전체 문서」를 누르면 다시 모든 문서가 표시됨
- 「RFP」 안에 하위 폴더 「2026」을 만들고 문서를 넣으면 하위 폴더와 그 문서도 「제한 · 사업팀」을 따름
- 「RFP」 범위를 따르는 문서의 열람 범위를 「개별 지정」 + 「제한」(대상 없음)으로 저장하면, 「RFP」를 볼 수 있는 다른 사용자에게도 그 문서는 보이지 않음 / 개별 지정한 문서를 「폴더 범위 따름」으로 되돌리면 다시 폴더 범위대로 보임
- 문서 상세에서 폴더를 「인사/채용」으로 바꾸면 트리에서 「채용」을 눌렀을 때 그 문서가 표시됨 / 열람만 가능한 다른 사용자의 문서는 폴더를 바꿀 수 없음
- 폴더 범위를 따르는 문서를 열람 범위가 다른 폴더로 옮기면 … 옮긴 뒤에는 새 폴더의 범위를 따름 (확인 안내는 화면 phase)
- 폴더 이름을 바꾸면 … 그 폴더 문서의 상세 화면에 새 이름이 표시됨 → 상세는 폴더 이름을 저장하지 않고 조회 때 읽는다
- [Pre-condition] 폴더에 들지 않은 문서 → 「제한」 + 사용자/그룹 저장 (기존 동작 유지)

## 읽어야 할 파일

- `backend/openarchive/services/documents.py` — `create_document`, `create_text_document`, `_insert_document`, `_create_once`·`_request_hash`(멱등 지문), `get_document`, `get_access`·`set_access`·`_read_access`, `list_documents`·`count_documents`(m24-doc-finder가 추가), `_load_owner_document`
- `backend/openarchive/services/folders.py` — step 3 (`ensure_folder_visible`, `folder_path`, 예외)
- `backend/openarchive/services/auth.py` — `delete_user`(139행 근처), `UserOwnsDocuments`
- `backend/openarchive/api/admin.py` — 사용자 삭제 오류 문구
- `backend/tests/test_documents.py`, `test_document_access.py`, `test_folders.py`, `test_folder_visibility.py`

## 작업

### 1) 테스트 먼저 — `backend/tests/test_document_folders.py`(새 파일) + 필요하면 기존 파일

1. `create_document(..., folder_id=인사)` → `folder_id`가 저장되고 `follows_folder = true`. 문서 자신의 `visibility`는 `'private'`, 문서 부여 0건(폴더 밖으로 나가면 소유자만 보는 쪽으로 닫힌다). `create_text_document`도 같다.
2. 볼 수 없는 폴더를 지정 → `FolderNotFound`이고 문서가 만들어지지 않는다.
3. 폴더 지정과 함께 `visibility`·부여 대상을 명시하면 거부한다(조용히 무시하지 않는다) — 메시지 "폴더에 넣는 문서는 폴더의 열람 범위를 따릅니다. 개별 지정은 문서 상세에서 합니다."
4. 멱등 키가 같아도 `folder_id`가 다르면 다른 요청으로 본다(지문에 폴더 포함). 폴더 없는 기존 요청의 지문은 바뀌지 않는다.
5. `move_document(…, folder_id=채용)`: 소유자만. 소유자가 아니면 기존 쓰기 권한 오류와 같은 경로(볼 수 있으면 권한 오류, 못 보면 없는 문서). 대상 폴더를 볼 수 없으면 `FolderNotFound`. `folder_id=None`이면 폴더에서 뺀다. `follows_folder`는 바꾸지 않는다.
6. 「폴더 범위 따름」 문서를 「RFP」(제한·사업팀)로 옮기면 lee 목록에서 사라진다.
7. `set_access(…, follows_folder=False, visibility='private', users=[], groups=[])` → 개별 지정. 「RFP」를 볼 수 있는 kim도 못 본다. `set_access(…, follows_folder=True)` → 폴더 범위로 복귀하며 문서 자신의 값은 그대로 보존된다. 폴더 없는 문서에 `follows_folder`를 주면 무시하지 말고 거부한다. 기존 호출(인자 없음)은 그대로 동작한다.
8. `get_access` 응답에 `follows_folder`, `folder`(볼 수 있으면 `{id, name, path}`, 아니면 null), `folder_scope`(볼 수 있으면 최상위 범위 요약) — 소유자 전용은 기존과 같다.
9. `get_document` 응답에 `folder`: 그 사용자가 폴더를 볼 수 있으면 `{id, name, path:[{id,name},…]}`, 볼 수 없으면 **null**(D5). 폴더 이름을 바꾸면 다음 조회에서 새 이름.
10. `list_documents(…, folder_id=X)`·`count_documents(…, folder_id=X)` → X에 **직접** 든 문서만(하위 폴더 제외). 볼 수 없는 폴더 id를 주면 0건(오류 아님 — 존재를 알리지 않는다).
11. 목록 요약(`SUMMARY_COLUMNS`)에 `folder_id`를 **넣지 않는다** — 볼 수 없는 폴더의 id가 새기 때문이다. 이 결정을 테스트로 고정: 볼 수 없는 폴더 안 개별 공개 문서의 목록 항목에 폴더 흔적이 없다.
12. `delete_user`: 폴더를 만든 계정 삭제 → 거부(문서 소유 거부와 같은 방식, 메시지는 "소유한 문서나 만든 폴더가 있어 삭제할 수 없습니다." 어조로 두 경우를 아우른다).

### 2) 구현 — `services/documents.py`, `services/auth.py`

```python
async def create_document(..., folder_id: UUID | None = None) -> ...
async def create_text_document(..., folder_id: UUID | None = None) -> ...
async def move_document(conn, document_id: UUID, *, user_id: str, folder_id: UUID | None) -> dict: ...
async def set_access(conn, document_id, *, user_id, visibility, users, groups, follows_folder: bool | None = None) -> dict: ...
async def list_documents(..., folder_id: UUID | None = None) / count_documents(..., folder_id=None)
```

- 폴더 확인은 `services/folders.py`의 `ensure_folder_visible`을 재사용한다(문서 소유자 = 업로드 사용자의 시점).
- 폴더 필터는 목록·건수의 **공유 WHERE 조각**에 더한다(m24-doc-finder가 만든 한 곳 — 이 phase는 m24 머지 뒤에 실행한다).
- `move_document`·`set_access`는 문서 행을 `FOR NO KEY UPDATE`로 잠근다(기존 잠금 방식). 감사 기록은 step 1 트리거가 `folder_id`·`follows_folder` 변경에서 남긴다.
- `delete_user`의 확인 쿼리에 `folders.created_by`를 더한다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_document_folders.py tests/test_documents.py tests/test_document_access.py tests/test_folders.py tests/test_folder_visibility.py tests/test_audit_log.py -q
cd backend && .venv/bin/pytest tests/test_auth.py tests/test_auth_api.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: 업로드 시 문서 `visibility`를 `'private'`로 두지 않고 요청값(`public`)을 저장하면 테스트 1이, 상세의 폴더 가시성 확인을 빼면 테스트 9(D5)가, 멱등 지문에서 폴더를 빼면 테스트 4가 실패해야 한다.
3. `phases/m25-folders/index.json`의 step 4를 갱신한다.

## 금지사항

- 폴더 안 문서의 실효 범위를 문서 행에 복사해 저장하지 마라. 이유: ADR-054 결정 6 — 조회 시점 판정. 복사하면 폴더 범위 변경이 문서에 반영되지 않는다.
- 볼 수 없는 폴더의 id·이름·경로를 어떤 응답에도 넣지 마라. 이유: D5, CLAUDE.md CRITICAL(볼 수 없는 것은 존재하지 않는 것처럼).
- 폴더 이름을 문서 행에 저장하지 마라. 이유: 이름 변경이 상세에 바로 보여야 한다(시험항목).
- 앱에서 `embedding_jobs`·`audit_log`에 INSERT하지 마라. 이유: CLAUDE.md CRITICAL.
- 기존 테스트를 깨뜨리지 마라
