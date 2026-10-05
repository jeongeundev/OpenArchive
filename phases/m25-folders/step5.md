# Step 5: search-folder

벡터 검색에 `hnsw.iterative_scan = strict_order`를 켜고(D7), 검색에 **폴더 필터(하위 폴더 포함)**를 더한다. 둘 다 단일 SQL 안에서.

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

## 현재 검색

- `services/search.py`: `SEARCH_SQL`의 후보 CTE `candidates`가 `document_chunks c JOIN documents d`에 태그·유형·열람 술어를 걸고 `ORDER BY c.embedding <=> qvec LIMIT k * CANDIDATE_MULTIPLIER`. 그 뒤 그래프 순회(`walk_ids`)가 이웃 문서를 거리 벌점과 함께 더한다(태그·유형·열람 술어 반복).
- 상수: `EF_SEARCH = 200`, `CANDIDATE_MULTIPLIER = 5`, `MAX_K = 20`. 불변식 `MAX_K * 배수 < EF_SEARCH`를 `test_search.py`의 `test_candidate_limit_stays_below_ef_search`와 `test_related.py`가 고정한다(CLAUDE.md CRITICAL).
- `apply_vector_search_settings`(259행 근처)가 같은 트랜잭션에서 `SET LOCAL hnsw.ef_search = 200` · `SET LOCAL random_page_cost = 1.1` · `SET LOCAL jit = off`를 건다. `test_search_issues_all_tunings_inside_the_query_transaction`이 실행된 문장 순서를 받아 적어 단언한다.
- `hnsw.iterative_scan`은 지금 어디서도 켜지 않으며, `tests/test_indexes.py` 147행 근처 docstring·`004_indexes.sql` 주석·`docs/ARCHITECTURE.md`가 "켜지 않는다(ADR-011 보강 3)"라고 적고 있다.
- 명세서: 「검색 화면에서 폴더 「인사」를 고르고 검색하면 **직접 맞은 결과는** 「인사」와 그 하위 폴더의 문서만 나옴」, 「「RFP」의 열람 범위에 그룹 「개발팀」을 더해 저장하면 … 개발팀 구성원이 「RFP」와 하위 폴더의 문서를 목록·검색에서 볼 수 있음」.
- **폴더 필터는 후보(직접 맞은 결과)에만 건다.** 그래프 순회로 붙는 관계 문서는 폴더 밖일 수 있다 — 명세서 문구가 "직접 맞은 결과는"으로 한정한 이유다. 순회 결과는 지금처럼 열람 술어·태그·유형만 따른다.

## 읽어야 할 파일

- `backend/openarchive/services/search.py` — 전부
- `backend/openarchive/api/schemas.py` — `SearchRequest`
- `backend/openarchive/services/answer.py` — 근거 수집이 검색을 어떻게 부르는지(폴더 인자는 이번에 더하지 않는다)
- `backend/tests/test_search.py` — 660~720행(튜닝 문장 단언·누수 테스트), 픽스처
- `backend/tests/test_indexes.py` — 147행 근처 docstring
- `/docs/ADR.md` — ADR-011(보강 3·4·5), ADR-054

## 작업

### 1) 테스트 먼저 — `backend/tests/test_search.py`에 추가·수정

1. 튜닝 문장 단언을 고친다: `SET LOCAL hnsw.iterative_scan = strict_order`가 같은 트랜잭션 안, `SEARCH_SQL` 전에 실행된다. 누수 테스트에 이 값도 넣는다(트랜잭션 뒤 세션 값이 원래대로).
2. `search_documents(…, folder_id=인사)` → 직접 맞은 결과(`via` 없는 결과)가 「인사」와 하위 폴더(깊이 2 이상 포함) 문서뿐이다. 볼 수 없는 폴더 id → 직접 결과 0건, 오류 없음.
3. 좁은 열람 범위의 recall: 사용자가 전체 문서의 소수만 보는 픽스처에서 그 사용자의 결과가 `k`를 채운다(볼 수 있는 관련 문서가 k개 이상 있을 때). **iterative_scan을 끄면 이 테스트가 실패하는지 확인하라.** 로컬 픽스처 규모에서 플래너가 Seq Scan을 골라 판별이 안 되면, 테스트 안에서 규모를 키우거나(합성 벡터는 `count(DISTINCT embedding::text)`로 퇴화를 먼저 확인 — CLAUDE.md) 그래도 안 되면 그 사실을 summary에 적고 테스트 1(문장 단언)로 설정 존재를 고정한다. 판별력 없는 recall 테스트를 "통과"로 남기지 마라.
4. 불변식 테스트(`MAX_K * 배수 < EF_SEARCH`)는 그대로 통과한다 — 고치지 마라.

### 2) 구현

- `apply_vector_search_settings`에 `SET LOCAL hnsw.iterative_scan = strict_order`를 더한다. `relaxed_order`는 쓰지 않는다 — 결과 순서가 보장되지 않아 후보 CTE의 `ORDER BY … LIMIT` 의미가 흐려진다(스파이크).
- `search_documents(…, folder_id: UUID | None = None)`: 후보 CTE에 `(%(folder)s::uuid IS NULL OR <d.folder_id가 folder 또는 그 하위>)`. 하위 판정은 문서의 폴더에서 조상으로 올라가며 `folder`를 만나는지 보는 **상관** 재귀 `EXISTS` 형태로 쓴다(비상관 `IN (하위 폴더 목록)` 금지 — 공통 배경). 볼 수 없는 폴더는 열람 술어 때문에 어차피 결과가 비지만, 폴더 필터 자체도 `FOLDER_VISIBLE_TO_USER`를 거쳐 존재를 드러내지 않게 한다.
- `SearchRequest`에 `folder_id: UUID | None = None`을 더하고 라우터가 넘긴다(`api/search.py`의 한 줄 — 같은 step에서 해도 된다).
- `tests/test_indexes.py` docstring의 "iterative_scan을 켜지 않는다" 서술을 이 결정에 맞게 고친다. `004_indexes.sql`의 주석은 적용된 마이그레이션이므로 고치지 않는다. 문서(`ARCHITECTURE.md`·ADR-011 개정 표기)는 step 7이 한다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_search.py tests/test_related.py tests/test_indexes.py tests/test_answer.py tests/test_answers.py tests/test_folder_visibility.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: `iterative_scan` 줄을 지우면 테스트 1(그리고 가능하면 3)이, 폴더 필터에서 하위 판정을 직속 폴더만으로 바꾸면 테스트 2가 실패해야 한다.
3. 로컬 DB에서 폴더 필터를 건 `SEARCH_SQL`을 `EXPLAIN`해 후보 CTE가 HNSW를 쓰는지 확인하고 결과를 summary에 적는다(규모가 작아 Seq Scan이면 그 규모도 적는다).
4. `phases/m25-folders/index.json`의 step 5를 갱신한다.

## 금지사항

- 후보를 넓게 가져와 파이썬에서 폴더로 거르지 마라. 이유: CLAUDE.md CRITICAL — 정형 필터 + 벡터 유사도는 단일 SQL.
- `EF_SEARCH`·`CANDIDATE_MULTIPLIER`·`MAX_K`를 바꾸지 마라. 이유: 불변식과 Seq Scan 벽(400) 사이의 값이다(ADR-011 보강 4).
- `BEGIN READ ONLY`로 바꾸지 마라. 이유: OpenProxy가 Replica로 보낸다(ADR-010).
- `walk_ids`(그래프 순회)에 폴더 필터를 넣지 마라. 이유: 명세서가 "직접 맞은 결과"만 한정했다 — 관계 문서는 폴더 밖일 수 있다.
- 기존 테스트를 깨뜨리지 마라
