# Step 2: search-symmetric

## 배경 — edge는 이제 한 방향으로만 저장된다

step 0(`backend/migrations/014_edges_triggers.sql`)부터 `document_edges`는 **계산한 문서가
`src_document_id`인 행만** 저장한다. 이전(008)에는 A가 계산한 관계를 `(A→B)`·`(B→A)` 두 행으로
넣었고, 재임베딩 때 `src=NEW OR dst=NEW`를 전부 지웠기 때문에 남이 발견한 관계까지 사라졌다
(#93 R4). 지금은 A가 계산하면 `(A→B)` 하나, B가 계산하면 `(B→A)` 하나이며 각자 자기 행만
교체한다.

그래서 관계를 **읽는 쪽이 양방향으로 읽어야** 한다. 조회 대칭은 ADR-029 개정의 일부다 —
"관계는 무방향이고, 어느 쪽이 발견했든 이어진다".

이 step은 `backend/app/services/search.py`의 그래프 순회만 고친다. 관련 문서·태그 추천
(`related.py`)은 step 3이다. `clusters.py`는 이미 쌍으로 접어 읽고 `diagnostics.py`는 이미
`src=X OR dst=X`로 읽으므로 바꾸지 않는다.

## 읽어야 할 파일

- `CLAUDE.md` — "검색은 단일 SQL 쿼리", "plain BEGIN…COMMIT", "후보 LIMIT < ef_search",
  "문서당 1건은 벡터 정렬 + LIMIT → DISTINCT ON → 재정렬"
- `docs/ARCHITECTURE.md` 「검색 데이터 흐름」 — 재귀 CTE `walk_ids`·`walk_targets`·`walk`의 역할
- `docs/ADR.md` **ADR-029**(결정 1: 노드는 문서, 청크는 부가 정보) · **ADR-011 보강 6**(순회 행마다 청크 정렬을 돌리지 않는 이유)
- `backend/app/services/search.py` — **수정 대상.** `SEARCH_SQL`의 `traversal_edges` CTE
- `backend/migrations/014_edges_triggers.sql` — 단방향 저장의 정의 (step 0 산출물)
- `backend/tests/test_search.py` — **테스트 추가 대상.** `test_relation_expands_search_to_a_document_outside_vector_candidates`(edge를 직접 INSERT하는 방식)와 `test_expanded_hit_excerpt_follows_the_edge_target_chunk`를 본떠라. 픽스처 `worker_conn`·`search_conn`, 헬퍼 `insert_test_document`·`process_all_embedding_jobs`

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_search.py`

1. `test_reverse_stored_edge_expands_search` — `test_relation_expands_search_to_a_document_outside_vector_candidates`와
   같은 픽스처(직접 진입점 `entry`, 관계로만 도달하는 `related`)에서 edge를 **반대 방향**
   `(related → entry, 'related', src_chunk_index 0, dst_chunk_index 0, 0.9)`로 하나만 넣는다.
   결과는 기존 테스트와 **같아야** 한다: `[entry, related]`, `hits[1].via.from_document_id == entry`,
   `kind == 'related'`, `depth == 1`.
2. `test_reverse_edge_excerpt_uses_the_stored_source_chunk` — 3청크 이상의 `related` 문서를 만들고
   (`test_expanded_hit_excerpt_follows_the_edge_target_chunk` 방식), edge를 반대 방향
   `(related → entry, src_chunk_index = 2, dst_chunk_index = 0)`으로 넣는다. entry에서 related로
   확장된 히트의 `chunk_index`는 **2**여야 한다 — 역방향으로 읽을 때 `dst_chunk_index` 자리에
   저장된 `src_chunk_index`가 온다.
3. `test_an_edge_stored_in_both_directions_is_expanded_once` — `(entry→related)`와 `(related→entry)`를
   둘 다 넣어도 related는 확장 결과에 **한 번만** 나온다. 기존 `walk_targets`의 `DISTINCT ON`이
   이를 접는지 고정한다.

### 2) 구현 — `backend/app/services/search.py`

`traversal_edges` CTE에 역방향을 더한다:

```sql
traversal_edges AS (
    SELECT e.src_document_id, e.dst_document_id, e.kind, e.dst_chunk_index
    FROM document_edges e
    UNION ALL
    -- 단방향 저장(014)의 역방향. 계산 주체가 어느 쪽이든 관계는 무방향이다 (ADR-029 개정).
    SELECT e.dst_document_id, e.src_document_id, e.kind, e.src_chunk_index
    FROM document_edges e
    UNION ALL
    SELECT src_document_id, dst_document_id, kind, dst_chunk_index
    FROM resolved_links
),
```

`overlaps`는 chunk_index가 NULL이라 뒤집어도 NULL이다. 그 밖의 CTE·정렬·`DISTINCT ON` 규칙은
바꾸지 않는다. 주석에 014와 ADR-029 개정을 가리켜 둔다.

`EF_SEARCH`·`CANDIDATE_MULTIPLIER`·`MAX_K` 불변식(`test_candidate_limit_stays_below_ef_search`)은
그대로다 — 이 step은 벡터 정렬을 건드리지 않는다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/python -m pytest tests/test_search.py tests/test_search_api.py tests/test_mcp_server.py -q
cd backend && .venv/bin/python -m pytest -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 검색이 여전히 **단일 SQL**인가? (역방향을 Python에서 합치지 않았는가)
   - `SET LOCAL hnsw.ef_search`·`random_page_cost`가 `apply_vector_search_settings`에 그대로 있는가?
   - `related.py`·`clusters.py`·`diagnostics.py`·마이그레이션을 건드리지 않았는가?
3. 결과에 따라 `phases/m13-relation-rules/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `related.py`를 고치지 마라. 이유: step 3의 일이다.
- 순회 상한(노드당 edge 수·kind 제한)을 추가하지 마라. 이유: 문서당 5건 상한(step 0)이 밀도를
  1/4로 낮춰 순회 행이 줄어드는지 **실 VM에서 먼저 잰다**(#93 P2). 재지 않은 상한은 넣지 않는다.
- `enable_seqscan`을 검색 트랜잭션에 걸지 마라. 이유: 재귀 순회 조인이 폭주한다(290초+, #93 P2).
- 역방향 edge를 위해 저장 형식·인덱스를 바꾸지 마라. 이유: `idx_document_edges_dst_kind`(007)가
  역방향 조인을 이미 받친다.
- 기존 테스트를 깨뜨리지 마라.
