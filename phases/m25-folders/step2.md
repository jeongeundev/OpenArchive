# Step 2: folder-predicate

`services/visibility.py`의 열람 술어에 **폴더 범위 상속**을 넣고, 같은 판정을 쓰는 **폴더 열람 술어**를 더한다. 술어가 하나라서 검색·목록·관련 문서·그래프·집계·위키링크·답변 근거·MCP가 이 변경 하나로 함께 바뀐다(CLAUDE.md CRITICAL, ADR-018·027).

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

## 현재 술어

`VISIBLE_TO_USER`(별칭 `d`, 바인딩 `%(user)s` — 사용자명 · `share:<uuid>` · NULL(익명)):

```
CASE WHEN %(user)s LIKE 'share:%%' THEN <공유 부여 EXISTS>
ELSE (d.visibility = 'public' OR d.owner_id = %(user)s OR <사용자·그룹 부여 EXISTS>) END
```

모듈 주석: 테이블과 주체 값 하나만 참조한다 — `%(user)s`를 `current_setting('app.principal')`로 바꾸면 그대로 RLS 정책의 `USING` 절이 되는 형태(ADR-044 결정 4). 사용처는 `services/` 8개 모듈 20여 곳이며 전부 `documents d`다.

## 읽어야 할 파일

- `backend/openarchive/services/visibility.py` — 전부(주석의 함정 설명 포함: 공유 id 캐스팅, 익명 NULL, `%%`)
- `backend/openarchive/migrations/030_folders_tables.sql` — step 0
- `backend/tests/test_visibility.py`, `backend/tests/test_share_visibility.py`, `backend/tests/test_grants.py` — 술어 테스트 선례·픽스처
- `/docs/ADR.md` — ADR-027, ADR-044(결정 4), ADR-054

## 작업

### 1) 테스트 먼저 — 새 `backend/tests/test_folder_visibility.py`

픽스처: 사용자 `kim`(사업팀) · `lee`(개발팀) · `park`(사업팀+개발팀) · `boss`(폴더 만든 사람, 그룹 없음) · `writer`(문서 소유자, 그룹 없음) · `admin`(is_admin, 그룹 없음). 폴더 안 문서의 소유자는 `writer`로 둔다 — 소유자 분기와 폴더 분기를 섞지 않기 위해서다. 문서는 SQL로 직접 넣는다. 각 판정을 `list_documents`(서비스)와 `SELECT … FROM documents d WHERE <술어>` 둘 다로 확인해도 좋다.

1. 폴더 없는 문서는 지금과 똑같이 판정된다(기존 테스트가 그대로 통과하는 것으로 갈음해도 된다).
2. `boss`의 최상위 「RFP」(private + 그룹 사업팀) 안의 「폴더 범위 따름」 문서(소유자 writer): kim·park은 보고 lee는 못 본다. **boss(만든 사람, 사업팀 아님)도 본다.** admin은 못 본다.
3. 같은 문서가 깊이 3 하위 폴더(「RFP/2026/1분기」) 안에 있어도 2와 같다.
4. 「RFP」 부여에 그룹 개발팀을 더하면 lee가 본다 — 문서를 고치지 않고. 빼면 다시 못 본다.
5. lee를 그룹 사업팀에 넣으면 본다, 빼면 못 본다.
6. 「RFP」 안의 문서를 「개별 지정」(`follows_folder=false`) + private + 부여 없음 → 소유자 writer만 본다. boss·kim·park은 못 본다. 「폴더 범위 따름」으로 되돌리면 2와 같다.
7. 「개별 지정」 + public인 문서가 **볼 수 없는 폴더**(「감사팀」, private, 부여 없음) 안에 있으면 lee도 본다(D5 — 폴더 정보 숨김은 서비스 step 3·4의 일).
8. 「폴더 범위 따름」 문서의 소유자는 폴더를 볼 수 없게 돼도 자기 문서를 본다.
9. 최상위 public 폴더 안의 「폴더 범위 따름」 문서의 **문서 자신의 visibility가 private**이어도 조직 전체가 본다(폴더 범위가 이긴다).
10. 공유 주체(`share:<uuid>`): 폴더 범위와 무관 — 공유에 부여된 문서만 본다(public 폴더 안 문서라도 부여가 없으면 못 본다).
11. 익명(None): public 최상위 폴더 안 「폴더 범위 따름」 문서는 보고, private 폴더 안 문서는 못 본다.
12. 폴더 술어 `FOLDER_VISIBLE_TO_USER`(별칭 `f`): 같은 사용자들에 대해 「RFP」와 그 하위 폴더가 2·4·5와 같은 규칙으로 보이고/안 보인다. boss는 자기 제한 폴더를 본다. 공유 주체는 어떤 폴더도 못 본다. admin은 public 폴더만 본다.
13. 술어가 상관 `EXISTS` 형태다 — `VISIBLE_TO_USER` 문자열에 `IN (`가 폴더 판정에 쓰이지 않음을 확인하는 대신, 아래 검증 절차 3의 EXPLAIN으로 확인한다(문자열 grep 단언은 쓰지 마라 — 회피를 부른다).

### 2) 구현 — `backend/openarchive/services/visibility.py`

- **최상위 범위 판정 조각을 하나만 정의**하고, 문서 술어와 폴더 술어가 그 조각을 시작 폴더만 바꿔(`d.folder_id` / `f.id`) 쓴다. 조각: 시작 폴더에서 `parent_id`로 거슬러 올라가는 상관 `WITH RECURSIVE`, 최상위(`parent_id IS NULL`) 행에서 `visibility='public' OR created_by = %(user)s OR folder_grants(사용자·그룹) EXISTS`.
- 문서 술어의 비공유 분기:
  `d.owner_id = %(user)s OR CASE WHEN d.folder_id IS NOT NULL AND d.follows_folder THEN <최상위 범위(d.folder_id)> ELSE (d.visibility = 'public' OR <문서 부여 EXISTS>) END`
- 공유 분기·익명 처리·`%%` 이스케이프 등 기존 함정 처리는 그대로 둔다.
- `FOLDER_VISIBLE_TO_USER`: 공유 주체는 거짓, 그 밖은 `<최상위 범위(f.id)>`.
- 모듈 docstring·주석에 폴더 상속 규칙과 형태 선택 근거(상관 재귀 EXISTS, 비상관 IN 금지 — 스파이크 실측)를 기존 어조로 더한다. RLS 전환 메모: `folders`에 자기 참조 정책을 걸면 `infinite recursion detected in policy`가 났다(스파이크) — 그때 판정을 정책 없는 경로로 빼야 한다는 한 줄.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_folder_visibility.py tests/test_visibility.py tests/test_share_visibility.py tests/test_grants.py -q
cd backend && .venv/bin/pytest tests/test_search.py tests/test_related.py tests/test_clusters.py tests/test_diagnostics.py tests/test_links.py tests/test_answer.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인 — 각각 되돌려 실패하는지 본다:
   - `created_by = %(user)s` 분기 제거 → 테스트 2(boss)
   - `follows_folder` 조건 제거 → 테스트 6
   - 재귀 대신 직속 폴더만 보기 → 테스트 3
   - 공유 분기에 폴더 범위를 섞음 → 테스트 10
3. 형태 확인: 로컬 DB에서 실제 검색 쿼리(`services/search.py`의 `SEARCH_SQL`)를 문서 수백 건·청크 수천 건 규모로 `EXPLAIN`해 후보 CTE가 HNSW 인덱스(`idx_chunks_embedding`)를 쓰는지 본다. 규모가 작아 플래너가 Seq Scan을 고르면(1만 청크 미만에서 정상일 수 있다) 그 사실을 summary에 적는다 — 이 step에서 단언 테스트로 만들지는 않는다.
4. `phases/m25-folders/index.json`의 step 2를 갱신한다.

## 금지사항

- 폴더 판정을 비상관 `IN (SELECT …)`로 쓰지 마라. 이유: 스파이크 실측 — 3천 청크에서 HNSW를 버리고 generic plan에서 15배 느려졌다.
- 열람 술어에 `is_admin` 분기를 넣지 마라. 이유: CLAUDE.md CRITICAL — 관리자 권한만으로는 문서를 열람하지 못한다(ADR-040·044·054).
- 술어 밖(라우터·서비스)에서 폴더 범위를 따로 계산하거나 결과를 파이썬에서 거르지 마라. 이유: 술어가 하나여야 모든 경로가 같은 판정을 한다(ADR-018·027).
- 임시 테이블을 쓰지 마라. 이유: OpenProxy 풀 백엔드에서 다음 클라이언트로 샌다(ADR-022) — 중간 결과는 CTE로.
- `db.py`의 `prepare_threshold=None`을 건드리지 마라. 이유: generic plan에서 술어가 20→300ms(스파이크).
- 기존 테스트를 깨뜨리지 마라
