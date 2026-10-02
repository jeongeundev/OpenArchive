# Step 2: shares-predicate

열람 술어 `VISIBLE_TO_USER`가 공유 주체(`share:<uuid>`)에게 **그 공유에 부여된 문서만** 보이게 한다. 술어 하나가 전 경로(검색·관련·태그 추천·군집·진단·위키링크·목록·진행 집계·상세)에 걸리는 구조(ADR-018·027)는 그대로이고, 술어 한 곳만 바꾼다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 「공유 (2026-10-02, #97 c)」 결정 2·3, 「구현 형태」(술어는 바인딩 파라미터 하나, 테이블과 주체 값만 참조), ADR-011(HNSW·`random_page_cost`)
- `backend/openarchive/services/visibility.py` — 현재 술어
- `backend/openarchive/migrations/026_shares_tables.sql` — step 1(`shares`, `document_grants.share_id`)
- 술어 사용처: `services/search.py`, `related.py`, `clusters.py`, `diagnostics.py`, `links.py`, `documents.py`(`list_documents`, `document_progress`, `get_document`, `ensure_visible`, `get_document_version`)
- `backend/tests/test_visibility.py` — 650행 이후 부여 매트릭스 테스트(`visibility_conn` 픽스처, `granted`/`user_id`/`sees` 파라미터화)를 형태의 선례로
- `backend/tests/test_indexes.py` — `test_planner_can_use_the_hnsw_index_for_distance_ordering`(EXPLAIN 단언 형태)

## 작업

### 1) 테스트 먼저 — `backend/tests/test_visibility.py`(또는 새 `test_share_visibility.py`)

공유 행과 공유 부여는 테스트에서 SQL로 직접 넣는다(서비스는 step 3). 주체 값은 `f"share:{share_id}"`.

1. **시나리오(#97 검증 항목)**: 문서 100건(조직 공개·제한 섞어서, 소유자 둘 이상), 공유 S에 5건 부여(조직 공개 문서와 제한 문서를 둘 다 포함). 그 문서들 사이·밖으로 관계(edge)와 위키링크를 만들어 둔다(밖의 문서를 가리키는 위키링크, 밖의 문서와의 관계 포함). S 주체로:
   - 검색(벡터·그래프 순회 포함) 결과가 5건의 부분집합이다.
   - `list_documents`가 정확히 5건, `document_progress` 합계가 5다.
   - 관련 문서(`related`)·태그 추천이 5건 밖 문서를 내지 않는다.
   - 군집 크기 합·진단(고아·중복 후보·깨진 링크·미분류) 수치가 5건만으로 계산된 값과 같다 — 밖의 문서는 개수에도 안 잡힌다. 밖을 가리키는 위키링크는 "존재하지 않는 대상"과 구별되지 않는다.
   - 5건 밖 문서의 상세·버전·링크·백링크·관련은 `DocumentNotFound`(존재 은닉).
2. 공유 주체는 **조직 공개 문서라도 부여가 없으면 못 본다**. 공유 주체는 다른 공유 S2에 부여된 문서를 못 본다.
3. 사용자 주체는 공유 부여로 아무것도 더 보지 못한다(제한 문서를 공유 S에만 부여 → 소유자 외 사용자는 못 본다).
4. 익명(None)·사용자 주체의 기존 동작은 변하지 않는다(기존 테스트 전부 통과가 곧 단언).
5. 공유를 지우면 그 즉시 S 주체가 아무것도 못 본다(CASCADE된 부여).
6. **HNSW**: 검색 후보 쿼리(서비스가 실제로 쓰는 SQL — `services/search.py`의 상수를 그대로 쓴다)를 `SET LOCAL random_page_cost = 1.1`·`SET LOCAL enable_seqscan = off` 안에서 공유 주체와 사용자 주체로 각각 `EXPLAIN`하면 `idx_chunks_embedding`이 계획에 있다(`test_indexes.py`의 단언 형태를 따른다).

### 2) 구현 — `backend/openarchive/services/visibility.py`

- 바인딩 이름은 `%(user)s` 하나로 유지한다. 의미는 "주체 값"이다: 사용자명 · `share:<uuid>` · NULL(익명).
- 주체 값이 `share:`로 시작하면: 그 공유에 대한 `document_grants` 행이 있는 문서만 참(`g.share_id = <접두사 뗀 값>::uuid`). 소유자·조직 공개·사용자·그룹 분기는 적용하지 않는다.
- 아니면 기존 술어 그대로. **NULL은 기존처럼 조직 공개만 보는 분기로 가야 한다** — `NULL LIKE …`는 NULL이므로 분기 조건을 쓸 때 NULL이 공유 분기로도, "아무것도 안 보임"으로도 새지 않게 한다.
- `LIKE` 패턴의 `%`는 psycopg 바인딩 쿼리 안이므로 `%%`로 쓴다(술어는 항상 파라미터와 함께 실행된다).
- 술어는 테이블과 주체 값만 참조하는 순수 SQL로 유지한다(결정 4 — `current_setting('app.principal')`로 바꾸면 정책 `USING` 절이 되는 형태). 모듈 독스트링·주석에 공유 분기와 그 이유(조직 공개가 공유에 안 열림)를 적는다.
- 공유 주체 값을 만드는 함수 `share_principal(share_id: UUID) -> str`와 접두사 상수를 이 모듈에 둔다(step 3·5가 쓴다).

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_visibility.py tests/test_indexes.py tests/test_search.py tests/test_related.py tests/test_clusters.py tests/test_diagnostics.py tests/test_links.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① 공유 분기에 `OR d.visibility = 'public'`을 더함 → 테스트 2 실패 ② 공유 분기를 지워 공유 주체가 사용자 분기로 감 → 테스트 1 실패 ③ NULL 처리를 바꿔 익명이 공유 분기로 감(아무것도 안 보임) → 기존 익명 테스트 실패. 하나라도 통과하면 테스트를 보강한다.
3. **시나리오 테스트 픽스처가 실제로 관계·위키링크를 만들었는지** 찍어 확인한다(edge·link 행 수 > 0, 밖을 가리키는 것 포함). 비어 있으면 "밖이 안 보인다"가 공허하게 참이다.
4. `phases/m21-shares/index.json`의 step 2를 갱신한다. summary에 술어 형태·mutant 결과·EXPLAIN 결과를 적는다.

## 금지사항

- 바인딩 파라미터를 하나 더(`%(share)s`) 추가하지 마라. 이유: ADR-044 「공유」 결정 3 — 쿼리 13곳·서비스 시그니처·테스트 호출부 전부가 바뀐다.
- 술어 사용처의 쿼리를 고치지 마라. 이유: 술어 하나만 바꾸는 것이 이 구조의 핵심이다(ADR-018·027). 사용처를 고쳐야 통과한다면 멈추고 `blocked`로 사유를 적어라.
- 검색 결과를 애플리케이션에서 후처리 필터링하지 마라(CLAUDE.md CRITICAL).
- 라우터·인증을 고치지 마라. 이유: step 5의 범위다.
- 임시 테이블을 쓰지 마라.
- 기존 테스트를 깨뜨리지 마라
