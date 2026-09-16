# Step 3: related-symmetric

## 배경 — 관련 문서·태그 추천이 아직 `src` 기준으로만 읽는다

step 0(`backend/migrations/014_edges_triggers.sql`)부터 `document_edges`는 **계산한 문서가
`src_document_id`인 행만** 저장한다. step 2는 검색의 그래프 순회를 양방향으로 바꿨다. 이 step은
`backend/app/services/related.py`의 두 쿼리 — 관련 문서(`RELATED_SQL`)와 태그 추천
(`TAG_SUGGESTION_SQL`) — 를 양방향으로 바꾼다.

지금 상태에서는 "B가 나중에 들어와 A를 발견했다"는 관계가 **A의 관련 문서에는 나오지 않는다**
(`(B→A)` 행만 있고 `src = A`로만 읽으므로). 이것이 #93 R4의 화면 쪽 증상이다.

같은 문서가 양방향에서 **서로 다른 kind**로 나올 수 있다 — A 기준으로는 두 대목 모두 닿아
`overlaps`, B 기준으로는 처리 시점의 후보가 달라 `related`. 이때 **`overlaps`를 남긴다**
(kind 사전순이 우연히 `overlaps < related`이지만 우연에 기대지 말고 `CASE`로 우선순위를 적어라 —
`search.py`의 `VIA_KIND_PRIORITY`가 같은 축을 이미 쓴다).

## 읽어야 할 파일

- `CLAUDE.md` — "볼 수 없는 문서는 존재하지 않는 것처럼 보인다", "주체 문서의 열람 검증은 서비스가 `ensure_visible`로"
- `docs/ARCHITECTURE.md` 「관련 문서·태그 추천」 — 공통 규칙 3개(열람 범위·`not_indexed`·`no_edges`)와 SQL 설명
- `docs/ADR.md` **ADR-029 결정 5**(관련 문서를 저장 관계 위로) · **ADR-027**(권한 규칙)
- `backend/app/services/related.py` — **수정 대상.** `RELATED_SQL`·`TAG_SUGGESTION_SQL`. 나머지(`IDENTICAL_SQL`, `find_related`·`suggest_tags`의 흐름, `reason`)는 그대로
- `backend/app/services/search.py` — `VIA_KIND_PRIORITY`(kind 우선순위 표현을 참고만 한다. import하지 마라 — 정렬 SQL은 모듈마다 자기 것을 갖는다)
- `backend/tests/test_related.py` — **테스트 추가 대상.** 픽스처 `worker_conn`·`related_conn`, 헬퍼 `insert_test_document`·`process_all_embedding_jobs`. edge를 직접 넣는 방식은 `backend/tests/test_search.py`의 `test_relation_expands_search_to_a_document_outside_vector_candidates`를 참고
- `backend/app/api/schemas.py`의 `RelatedDocumentItem`·`TagSuggestionItem` — **응답 형태 변경 없음**

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_related.py`

edge는 워커를 돌린 뒤 `DELETE FROM document_edges`로 비우고 직접 INSERT한다(FakeProvider 벡터로
트리거가 만드는 edge는 임의라, 방향을 통제하려면 직접 넣어야 한다).

1. `test_related_documents_include_a_neighbour_that_computed_the_edge` — `source`·`other` 두 문서.
   edge를 **`(other → source, 'related', 0, 0, 0.8)`만** 넣는다. `find_related(source)`의 items에
   `other`가 kind `related`·score 0.8로 나온다. `reason`은 `None`.
2. `test_a_neighbour_reached_from_both_directions_is_listed_once_with_overlaps_first` —
   `(source → other, 'related', 0, 0, 0.7)`와 `(other → source, 'overlaps', NULL, NULL, 1.0)`을 둘 다
   넣는다. items에 `other`가 **정확히 한 번**, kind `overlaps`, score 1.0으로 나온다.
3. `test_related_documents_from_reverse_edges_apply_visibility` — `(private_other → source)` 방향으로만
   있는 edge에서, `private_other`가 다른 사용자의 private 문서면 요청자에게 나오지 않는다
   (`test_related_documents_apply_candidate_visibility`의 방식).
4. `test_tag_suggestions_count_tags_of_neighbours_that_computed_the_edge` — `(other → source)`만
   있고 `other`의 태그가 `["운영", "정합성"]`, source의 태그가 `["정합성"]`이면 추천은
   `[("운영", 1)]`이다.
5. `test_tag_suggestions_count_a_neighbour_once_even_if_stored_in_both_directions` —
   `(source→other)`·`(other→source)` 둘 다 있어도 `other`의 태그 빈도는 1이다.

기존 `test_related_documents_are_ranked_by_score_and_exclude_the_source`·정렬·`no_edges`·
`not_indexed` 테스트는 그대로 통과해야 한다.

### 2) 구현 — `backend/app/services/related.py`

두 쿼리에 공통 이웃 CTE를 둔다. 시그니처 수준:

```sql
WITH neighbors AS (
    SELECT e.dst_document_id AS document_id, e.kind, e.score
    FROM document_edges e
    WHERE e.src_document_id = %(id)s
    UNION ALL
    -- 단방향 저장(014)의 역방향 — 남이 발견한 관계도 이 문서의 관련 문서다 (ADR-029 개정)
    SELECT e.src_document_id, e.kind, e.score
    FROM document_edges e
    WHERE e.dst_document_id = %(id)s
),
best AS (
    -- 같은 이웃이 양방향에서 다른 kind로 오면 overlaps를 남긴다. 그다음 높은 score.
    SELECT DISTINCT ON (document_id) document_id, kind, score
    FROM neighbors
    ORDER BY document_id,
             CASE kind WHEN 'overlaps' THEN 0 WHEN 'related' THEN 1 ELSE 2 END,
             score DESC
)
SELECT d.id, d.title, d.tags, b.kind, b.score
FROM best b
JOIN documents d ON d.id = b.document_id
WHERE {VISIBLE_TO_USER}
ORDER BY b.kind, b.score DESC, d.id
LIMIT %(k)s
```

`TAG_SUGGESTION_SQL`의 `neighbors` CTE도 같은 `best`를 기반으로 상위 `NEIGHBOR_LIMIT`을 고른다.
최종 정렬(`ORDER BY e.kind, e.score DESC, d.id`)과 `LIMIT`은 지금과 같다 — 결과 순서 의미를
바꾸지 않는다. 열람 조건은 이웃 문서에 그대로 건다(`documents d` JOIN 뒤 `VISIBLE_TO_USER`).

`ARCHITECTURE.md`의 SQL 발췌는 step 7에서 갱신하므로 여기서 문서를 고치지 않는다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/python -m pytest tests/test_related.py tests/test_related_api.py tests/test_mcp_server.py -q
cd backend && .venv/bin/python -m pytest -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 관련 문서·태그 추천이 여전히 **단일 SQL**인가? (양방향을 Python에서 합치지 않았는가)
   - `ensure_visible` 선행 검증과 `not_indexed`·`no_edges` reason이 그대로인가?
   - API 스키마·라우터·프론트엔드를 건드리지 않았는가?
3. 결과에 따라 `phases/m13-relation-rules/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `avg(embedding)` 같은 조회 시점 벡터 계산을 되살리지 마라. 이유: ADR-029 결정 5 — 관련 문서는 저장된 관계만 읽는다.
- `search.py`·`clusters.py`·`diagnostics.py`를 고치지 마라. 이유: step 2가 끝났고 나머지 둘은 이미 대칭으로 읽는다.
- `VIA_KIND_PRIORITY`를 `search.py`에서 import하지 마라. 이유: 검색과 관련 문서는 정렬 축이 다르고(검색은 거리·깊이가 앞), 모듈 간 SQL 조각 공유는 한쪽 변경이 다른 쪽을 조용히 바꾼다.
- 기존 테스트를 깨뜨리지 마라.
