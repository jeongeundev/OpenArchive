# Step 0: folder-tables

폴더 테이블·폴더 부여 테이블과 문서의 폴더 컬럼을 만드는 마이그레이션 `030_folders_tables.sql`을 쓴다. 스키마만 다룬다 — 열람 술어·서비스는 뒤 step이 한다.

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

## 읽어야 할 파일

- `/docs/ADR.md` — ADR-054(폴더), ADR-044(부여 축·`document_grants`), ADR-055(감사 — 이 step은 감사를 건드리지 않는다)
- `backend/openarchive/migrations/025_grants_tables.sql`, `026_shares_tables.sql` — `document_grants`의 CHECK·부분 유니크 인덱스 선례(PG14 지원 때문에 `NULLS NOT DISTINCT`를 쓰지 않는다)
- `backend/openarchive/migrations/002_tables.sql`, `009_auth_tables.sql` — `documents`, `users`
- `backend/openarchive/migrations/029_audit_triggers.sql` — 마지막 번호(이 step은 030)
- `backend/tests/test_tables.py` — `CORE_TABLES`(20행)와 제약 테스트 선례

## 작업

### 1) 테스트 먼저 — `backend/tests/test_tables.py`에 추가

1. `CORE_TABLES`에 `folders`, `folder_grants`를 더한다(마이그레이션 뒤 존재 확인).
2. 최상위 폴더(`parent_id NULL`)는 `visibility`가 있어야 하고, 하위 폴더는 `visibility`가 NULL이어야 한다 — 어기면 CHECK 위반.
3. `visibility`에 `'publik'` 같은 값 → CHECK 위반.
4. 이름이 `''`·`'  '`·`'a/b'` → CHECK 위반.
5. 같은 부모 아래 같은 이름 하위 폴더 → 유니크 위반. **같은 이름 최상위 폴더 둘은 허용된다.**
6. 하위 폴더가 있는 폴더 삭제 → FK 위반. 문서가 든 폴더 삭제 → FK 위반. 빈 폴더 삭제 → 성공.
7. `folder_grants`: 대상이 0개·2개 → CHECK 위반. 같은 폴더·같은 사용자 두 번 → 유니크 위반. 폴더를 지우면 부여도 지워진다.
8. `documents`에 `folder_id`(NULL 허용)·`follows_folder`(NOT NULL, 기본 true)가 있다. 기존 문서 INSERT(폴더 없이)가 그대로 성공한다.

### 2) 구현 — `backend/openarchive/migrations/030_folders_tables.sql`

공통 배경의 스키마 절을 그대로 만든다. 추가로:

- 인덱스: `documents(folder_id)`, `folders(parent_id)`, `folder_grants(folder_id)`, 대상별 보조 인덱스(그룹 구성원 → 폴더 부여 조회용 `folder_grants(group_id)`·`(user_id)`).
- 파일 머리 주석에 무엇을 왜(ADR-054 결정, D1 최상위만 범위, D2 폴더 이동 없음, 최상위 이름 유일성 없음의 이유)를 짧게 적는다. 기존 마이그레이션 주석의 어조를 따른다.
- 기존 문서는 `folder_id NULL`이므로 판정이 바뀌지 않는다 — 데이터 이전 없음.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_tables.py tests/test_migrations.py -q
cd backend && .venv/bin/pytest tests/test_documents.py tests/test_visibility.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: 최상위 범위 CHECK를 지우면 테스트 2가, 하위 폴더 이름 유니크를 지우면 테스트 5가 실패해야 한다.
3. `phases/m25-folders/index.json`의 step 0을 갱신한다.

## 금지사항

- 이미 적용된 마이그레이션 파일(001~029)을 고치지 마라. 이유: 이미 적용된 DB에는 다시 돌지 않는다 — 변경은 새 번호로만.
- 실효 범위(범위 출처 폴더·열람자 목록)를 저장하는 컬럼·트리거를 만들지 마라. 이유: ADR-054 결정 6 — 조회 시점 판정. 스파이크에서 저장형과 지연 차이가 0~3ms였고, 저장형은 틀리면 에러 없이 새는 파생 상태다.
- 최상위 폴더 이름에 유니크 제약을 걸지 마라. 이유: 공통 배경 스키마 절 — 존재 누출.
- `documents.visibility`에 CHECK를 더하지 마라. 이유: #193의 별도 범위다.
- 기존 테스트를 깨뜨리지 마라
