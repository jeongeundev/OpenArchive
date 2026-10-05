# Step 3: folder-service

폴더 만들기·트리 조회·이름 변경·삭제·폴더 열람 범위 조회/변경을 새 서비스 모듈 `services/folders.py`에 만든다. 문서 쪽 변경(폴더 지정·이동·개별 지정)은 step 4가 한다.

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

## 명세서 시험항목 중 이 step이 받치는 것 (문구가 구현 계약 — 따옴표 문구는 글자 그대로)

- 「새 폴더」로 폴더 「인사」를 만들면 폴더 트리에 나타남 / 「인사」 안에 하위 폴더 「채용」을 만들면 트리에 「인사」 아래 계층으로 표시됨
- 새로 만든 최상위 폴더의 열람 범위는 「조직 공개」, 하위 폴더는 「상위 폴더 범위 따름」으로 표시됨
- 폴더 이름을 바꾸면 트리와 그 폴더 문서의 상세 화면에 새 이름이 표시됨
- 문서가 든 폴더를 삭제하면 **"폴더가 비어 있지 않습니다."**로 거부됨 / 빈 폴더를 삭제하면 트리에서 사라짐
- 다른 사용자가 만든 폴더의 이름 변경·삭제는 거부됨 / 관리자는 볼 수 있는 폴더라면 다른 사용자가 만든 폴더라도 이름을 바꾸거나 삭제할 수 있음
- 폴더 「감사팀」의 열람 범위를 「제한」(대상 없음)으로 저장하면 만든 사람 외 다른 사용자의 폴더 트리에 「감사팀」이 나타나지 않음
- [Pre-condition] 「조직 공개」 폴더에 폴더 범위를 따르는 문서 1건과, lee가 볼 수 없게 개별 지정한 「제한」 문서 1건이 있음 → lee에게 그 폴더의 문서 수가 1로 표시되고 목록에도 1건만 나옴
- 폴더를 만들지 않은 일반 사용자가 그 폴더의 열람 범위를 저장하면 거부됨 / 관리자도 다른 사용자가 만든 폴더의 열람 범위는 저장할 수 없음
- [Pre-condition] 「RFP」가 「제한 · 사업팀」, lee는 사업팀이 아님 → 관리자가 lee를 사업팀에 넣으면 lee의 폴더 트리에 「RFP」가 나타남 / 빼면 사라짐
- 업로드 화면에서 폴더 「RFP」를 고르면 열람 범위가 「폴더 범위 따름(제한 · 사업팀)」으로 표시됨 → 트리 조회가 각 폴더의 **실효 범위 요약**(최상위 폴더의 visibility와 부여 대상 이름)을 그 폴더를 볼 수 있는 사용자에게 준다.

## 읽어야 할 파일

- `backend/openarchive/services/visibility.py` — step 2의 `VISIBLE_TO_USER`, `FOLDER_VISIBLE_TO_USER`
- `backend/openarchive/services/documents.py` — `set_access`(차이만 반영·`FOR NO KEY UPDATE` 잠금), `_check_grantees`, 예외 클래스 선례, `ensure_visible`
- `backend/openarchive/services/grants.py` — `resolve_grantees`, `insert_grants`
- `backend/openarchive/migrations/030_folders_tables.sql`, `031_folder_audit_triggers.sql`
- `backend/tests/test_document_access.py`, `backend/tests/test_grants.py` — 테스트 선례

## 작업

### 1) 테스트 먼저 — 새 `backend/tests/test_folders.py` (실제 DB, 서비스 직접 호출)

위 시험항목 각각을 서비스 수준으로 옮긴다. 추가로:

1. 볼 수 없는 부모 아래 하위 폴더 만들기 → `FolderNotFound`(없는 것과 같은 오류).
2. 하위 폴더에 범위 저장 → 거부(`SubfolderScope` 등, 메시지 "하위 폴더는 상위 폴더의 열람 범위를 따릅니다.").
3. 같은 부모 아래 같은 이름 → `FolderNameTaken`("같은 이름의 폴더가 이미 있습니다."). 같은 이름 최상위 폴더 둘은 각각 만들어진다(다른 사용자의 제한 폴더와 이름이 같아도 오류 없음).
4. 볼 수 없는 폴더의 이름 변경·삭제·범위 저장은 관리자를 포함해 `FolderNotFound`.
5. 하위 폴더만 있고 문서는 없는 폴더 삭제 → "폴더가 비어 있지 않습니다.". 내가 볼 수 없는 문서만 든 폴더도 같다.
6. 폴더 범위 저장은 차이만 반영한다 — 같은 값 재저장 시 `folder_access_changed` 감사 행이 늘지 않는다(step 1 트리거와 맞물림).
7. 트리 조회의 문서 수는 그 폴더에 **직접** 든, 그 사용자가 **볼 수 있는** 문서만 센다(하위 폴더 문서 제외).
8. 트리 조회의 각 폴더에 `can_manage`(만든 사람 또는 관리자)·`can_change_access`(최상위이고 만든 사람)가 맞게 나온다.
9. 공유 주체·익명은 private 폴더를 보지 못한다(공유 주체는 아무 폴더도).

### 2) 구현 — 새 `backend/openarchive/services/folders.py`

```python
class FolderNotFound(Exception): ...
class FolderNotEmpty(Exception): ...        # "폴더가 비어 있지 않습니다."
class FolderNameTaken(Exception): ...       # "같은 이름의 폴더가 이미 있습니다."
class SubfolderScope(Exception): ...        # "하위 폴더는 상위 폴더의 열람 범위를 따릅니다."
class NotFolderCreator(Exception): ...      # 범위 변경·이름 변경·삭제 권한 없음(메시지는 기존 문서 권한 오류 어조)

async def create_folder(conn, *, user_id: str, name: str, parent_id: UUID | None = None,
                        visibility: str = "public", grant_users: list[str] | None = None,
                        grant_groups: list[str] | None = None) -> dict: ...
async def list_folders(conn, *, user_id: str | None, is_admin: bool = False) -> list[dict]:
    """볼 수 있는 폴더 평면 목록 — id, parent_id, name, created_by, document_count,
    scope{visibility, users, groups}(최상위 기준), inherited(하위 폴더면 true), can_manage, can_change_access."""
async def ensure_folder_visible(conn, folder_id: UUID, *, user_id: str | None) -> dict: ...
async def folder_path(conn, folder_id: UUID) -> list[dict]:  # 최상위부터 [{id, name}, …]
async def rename_folder(conn, folder_id: UUID, *, user_id: str, is_admin: bool, name: str) -> dict: ...
async def delete_folder(conn, folder_id: UUID, *, user_id: str, is_admin: bool) -> None: ...
async def get_folder_access(conn, folder_id: UUID, *, user_id: str) -> dict: ...   # 만든 사람만
async def set_folder_access(conn, folder_id: UUID, *, user_id: str, visibility: str,
                            users: list[str], groups: list[str]) -> dict: ...
```

- 모든 조회·변경은 먼저 `FOLDER_VISIBLE_TO_USER`로 대상 폴더를 확인한다 — 볼 수 없으면 `FolderNotFound`(관리자 포함). 권한 판정은 그다음이다.
- `create_folder`: 하위 폴더면 `visibility`·부여를 받지 않는다(주면 `SubfolderScope`). 최상위 부여는 private일 때만(public에 부여 → 문서와 같은 오류 규칙). 부여는 이름 → id 해석을 먼저 하고 실패하면 아무것도 쓰지 않는다. 생성 시 부여는 CLI `import --grant-group`(다음 phase)이 쓴다.
- `set_folder_access`: **최상위 + 만든 사람만**. `is_admin` 인자를 받지 않는다 — 관리자 경로 자체가 없다. 폴더 행을 `FOR NO KEY UPDATE`로 잠그고, 부여는 `set_access`처럼 **차이만** DELETE/INSERT 한다(감사 기록이 실제 변경만 남게).
- `delete_folder`: 하위 폴더·문서가 하나라도 있으면(볼 수 있든 없든) `FolderNotEmpty`. FK RESTRICT 위반도 같은 예외로 바꾼다(동시 삽입 경합의 안전망).
- `list_folders`는 **한 SQL**로: 폴더 술어 + 최상위 범위 요약 + 직접 든 볼 수 있는 문서 수(`VISIBLE_TO_USER` 재사용). 파이썬에서 폴더를 거르지 마라.
- 범위 요약의 부여 대상 이름은 그 폴더를 볼 수 있는 사용자에게 보인다(업로드 화면의 「폴더 범위 따름(제한 · 사업팀)」 표시). 이 선택을 모듈 docstring에 한 줄 남긴다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_folders.py tests/test_folder_visibility.py tests/test_audit_log.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: `set_folder_access`에 관리자 허용을 넣으면 테스트(관리자 거부)가, 문서 수에서 열람 술어를 빼면 테스트 7(시험항목 「문서 수 1」)이, 볼 수 없는 폴더 확인을 권한 판정 뒤로 옮기면 테스트 4가 실패해야 한다.
3. `phases/m25-folders/index.json`의 step 3을 갱신한다.

## 금지사항

- 폴더 열람 범위 변경에 관리자 경로를 만들지 마라. 이유: ADR-054 결정 4 — 관리자가 범위를 바꿀 수 있으면 자기에게 열어 안의 문서를 읽게 된다(CLAUDE.md CRITICAL).
- 볼 수 없는 폴더에 대해 "권한 없음"(403 성격) 오류를 내지 마라 — `FolderNotFound`. 이유: 오류 종류가 존재를 누출한다(ADR-027).
- 앱에서 `audit_log`에 INSERT하지 마라. 이유: step 1의 트리거가 쓴다(ADR-055).
- 라우터·문서 서비스를 이 step에서 고치지 마라. 이유: step 4·6의 범위.
- 기존 테스트를 깨뜨리지 마라
