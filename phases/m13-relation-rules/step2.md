# Step 2: edges-rebuild

## 배경 — 관계 판정 규칙이 여러 청크 문서에서 무너졌다 (#93 → #94)

`document_edges`는 문서가 `ready`가 될 때 `008_edges_triggers.sql`의 `build_document_edges()`
트리거 함수가 만든다. 청크마다 다른 문서의 최근접 10개(HNSW)를 뽑아 문서쌍으로 모으고,
자기 청크 중 상대에게 닿은 비율이 0.8 이상이고 2개 이상이면 `overlaps`, 나머지는 `related`로
저장한다. 시연 코퍼스(64문서·**전부 1청크**)에서는 순도 0.70~0.86이었지만, 실 코퍼스
두 벌(#93: A = obsidian-help ko 176 + docs 8 → 184문서·1,080청크, C = KOCCA 규정집 104문서·
2,211청크, 실 BGE-M3)에서 아래 결함이 확정됐다.

| # | 결함 | 원인 |
|---|---|---|
| R1 | `overlaps` 정밀도 0.17 (C 143쌍 중 판본 24쌍만 진짜) | 비율의 분모가 **자기** 청크 수라, 2청크 문서는 걸리기만 하면 2/2 = 1.0. 긴 문서는 상대 모든 청크의 top-10에 한 청크씩 들어가기 쉽다 |
| R2 | 긴 문서가 허브 — degree 최대 92(C)·98(A), 청크 수와 상관 0.53 | 청크 top-10을 문서 edge로 **무조건** 승격 |
| R3 | `related` 무관 쌍 25쌍 중 16~17 | 상한이 없다. 거리 임계는 §15에서 기각(정오가 같은 거리대에 섞여 있다) |
| R4 | 문서 하나를 재임베딩하면 남의 관계까지 지워진다 | `DELETE WHERE src=NEW OR dst=NEW` 뒤 자기 기준 양방향 INSERT — 남이 발견한 행이 소실된다 |
| P1 | 1만 청크 미만에서 플래너가 HNSW를 안 고른다 — 10청크 4.6s·159청크 40s | `random_page_cost=1.1`에서도 Seq Scan 비용(433)이 인덱스(1,757)보다 싸게 계산된다 |

### 닫힌 결정 — 시뮬레이션으로 확정했다. 다시 판단하지 말고 아래대로 구현하라 (2026-09-16, #94)

덤프한 실 임베딩 위에서 규칙 후보를 돌리고 실제 저장 edge와 대조해(A 자카드 1.000) 고른 값이다.

| 항목 | 값 | 근거 |
|---|---|---|
| `NEIGHBOR_N` | **10 유지** | n=5와 순도 차이 ±0.01. `hnsw.ef_search=200`과의 불변식 여유 |
| `overlaps` 판정 | **양쪽 비율** — `matched_src / src_chunks ≥ 0.8` **AND** `matched_dst / dst_chunks ≥ 0.8` AND `matched_src ≥ 2` | C 정밀도 0.17 → 0.41, 판본 재현율 24/24 유지. 0.9는 판본 2쌍을 놓친다 |
| 문서당 이웃 상한 | **5건** — `ORDER BY matched_src DESC, min_dist ASC, dst_document_id` 상위 5 | deg_max 92→48·98→31, 순도 C 0.385→0.577·A 0.505→0.543. cap3은 덩어리가 잘게 갈려 하락 |
| 저장 방향 | **단방향** — `src_document_id` = 계산한 문서. `DELETE WHERE src_document_id = NEW.id`만 | 재실행 자카드 0.97→0.99, 남의 발견이 소실되지 않는다. 조회 측(`search.py`·`related.py`)은 **step 0·1에서 이미 양방향으로 읽는다** — 그래서 이 저장 변경이 조회 테스트를 깨지 않아야 한다 |
| 판정 본체 | **`rebuild_document_edges(uuid)` 일반 함수**로 빼고 트리거 함수는 호출만 | 순서 의존(처리 시점까지의 문서만 후보)은 트리거로 못 푼다 — 대량 적재 뒤 전량 재계산(step 5)이 이 함수를 쓴다 |
| P1 | `rebuild_document_edges`의 **함수 정의 `SET enable_seqscan = off`** (+ 기존 `hnsw.ef_search = 200`·`random_page_cost = 1.1`) | 트리거 전체 4.6s→0.2s·40s→2.7s(결과 동일). `SET LOCAL`은 OpenProxy 풀 백엔드에 남는 PL/pgSQL generic plan 때문에 안 먹는다 |

### 픽스처 원칙 — 프로브마다 가까운 청크가 10개 이상 있어야 한다

각 청크는 다른 문서의 청크 중 최근접 **10개**를 뽑고, 그 10개에 든 문서는 거리와 무관하게
"만난 것"으로 센다. 실 코퍼스(수천 청크)에서는 top-10이 전부 가깝지만, 작은 픽스처에서는
가까운 청크가 10개가 안 되면 **직교 청크(dist 1.0)가 top-10을 채운다.** 그러면 무관한 문서가
두 대목에서 만난 것(`matched_src = 2`)이 되어 상한 5의 앞자리를 차지하고, 긴 문서의 먼 청크가
`matched_dst`에 들어가 양쪽 비율이 1.0이 된다. 이 phase의 1차 실행이 정확히 이 이유로 테스트
3개가 깨져 막혔다. 아래 픽스처의 filler 개수(축마다 9개)는 그 계산에서 나온 값이니 줄이지 마라 —
"9개 + 목표 청크 1개 = 10"이라 목표 청크가 10번째 자리에 남고 직교 청크는 밀려난다.

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `CLAUDE.md` — CRITICAL 규칙. 특히 "임베딩 파이프라인의 트리거링은 DB 계층", "임시 테이블을 쓰지 않는다", "후보 LIMIT은 ef_search보다 작아야 한다"
- `docs/ADR.md` **ADR-029**(관계를 저장 시점에 만든다 — 결정 3·4와 정정 블록) · **ADR-022**(임시 테이블 금지) · **ADR-011**(HNSW 불변식)
- `docs/OPENSQL_RESEARCH.md` **§14**(관계 판정 신호 실측) · **§15**(덩어리 밀도와 이웃 개수 — 거리 임계가 안 되는 이유)
- `backend/migrations/008_edges_triggers.sql` — **교체 대상.** 청크별 상수 프로브 루프(JSONB 누적)와 CTE 구조는 재사용한다
- `backend/migrations/006_edges_tables.sql` · `007_edges_indexes.sql` — `uq_document_edges_relation` 유니크 인덱스, `idx_document_edges_src_kind`
- `backend/migrations/002_tables.sql` — `document_chunks`의 `UNIQUE (document_id, chunk_index)`가 문서별 조회의 인덱스다
- `backend/tests/test_triggers.py` — **수정 대상.** 헬퍼 `insert_document`·`mark_document_ready`·`unit_vector`·`edges_for`를 그대로 쓴다. 403행부터가 edge 테스트다
- `backend/app/migrations.py` — 마이그레이션 파일은 번호순으로 한 번씩만 적용된다 (`schema_migrations`)

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_triggers.py`

기존 edge 테스트 중 아래 셋은 이 step에서 **거짓이 되므로 재작성**한다. 나머지 edge 테스트는
그대로 통과해야 한다.

| 기존 이름 | 새 이름과 단언 |
|---|---|
| `test_non_directional_edges_are_inserted_in_both_directions_without_self_edges` | `test_edges_are_stored_once_from_the_document_that_computed_them` — first·second 순으로 ready. `edges_for(second)`의 `(src, dst)` 집합이 **정확히 `{(second, first)}`**. `(first, second)`는 없다(first가 ready일 때 second는 없었다). self edge 없음 |
| `test_reembedding_replaces_all_edges_touching_the_document` | `test_reembedding_replaces_only_the_rows_this_document_computed` — first·second ready 후 `(second→first)`의 score를 0.123으로 찍는다. **first**를 재임베딩(청크 삭제 → `processing` → `mark_document_ready`)하면 `(first→second)`가 새로 생기고 `(second→first)`의 0.123은 **그대로 남는다**. 이어서 **second**를 재임베딩하면 `(second→first)`가 교체되어 0.123이 사라지고 `(first→second)`는 남는다 |
| `test_edges_trigger_definition_and_function_settings_are_scoped` | 트리거 정의 단언 3개는 유지. `proconfig`는 **`rebuild_document_edges` 함수**(`pg_proc WHERE proname = 'rebuild_document_edges'`)에서 읽어 `{"hnsw.ef_search=200", "random_page_cost=1.1", "enable_seqscan=off"}`와 같아야 한다. 트리거 함수 `build_document_edges`의 `proconfig`는 `None`이다(설정은 판정 본체에만 있다) |

새로 추가할 테스트 5개:

1. `test_overlaps_requires_the_ratio_on_both_sides` — `long`(4청크, `unit_vector(0..3)`) ready.
   이어서 **filler** 1청크 문서를 축 0·1마다 9개씩(총 18개) ready — `unit_vector(axis)`에
   `vector[10] = 0.001`을 섞는다(long의 같은 축 청크보다 조금 멀고, 직교 청크보다 훨씬 가깝다).
   그 뒤 `short`(2청크, `unit_vector(0)`·`unit_vector(1)`) ready. short의 각 프로브 top-10은
   long의 같은 축 청크 1개 + filler 9개로 차서 long의 나머지 두 청크(dist 1.0)는 안 들어온다.
   short 기준 비율은 2/2 = 1.0이지만 long 쪽은 2/4 = 0.5다. `src = short` 행을
   `{dst: kind}`로 모아 **`kinds[long] == "related"`**를 단언한다(행 개수는 단언하지 마라 —
   filler도 이웃이라 상한 5까지 찬다). filler 없이 만들면 top-10에 long의 4청크가 전부 들어가
   `matched_dst = 4`로 overlaps가 된다(1차 실행에서 실측). (기존
   `test_two_matching_chunks_still_qualify_as_overlaps`가 양쪽 2/2인 경우를 커버한다.)
2. `test_a_document_keeps_at_most_five_neighbour_documents` — 1청크 후보 8개를 `unit_vector(0)`에
   `vector[k] = 0.01 * k`(k = 1..8)를 섞어 서로 다른 거리로 ready. 그 뒤 `source`(1청크,
   `unit_vector(0)`) ready. `src_document_id = source`인 행이 **정확히 5개**이고, 남은 dst는
   거리가 작은 순 5개(k = 1..5)다.
3. `test_neighbour_cap_prefers_documents_met_in_more_passages` — `twin`(2청크, 각각
   `unit_vector(0)`·`unit_vector(1)`에 `vector[5] = 0.05`를 섞어 약간 멀게, dist ≈ 0.00125) ready.
   1청크 문서를 축 0·1마다 **9개씩**(총 18개) ready — `unit_vector(axis)`에 `vector[6] = 0.001`을
   섞어 twin의 같은 축 청크보다 가깝게. 마지막에 `source`(2청크 `unit_vector(0)`·`unit_vector(1)`)
   ready. 각 프로브 top-10 = 같은 축 filler 9개 + twin의 같은 축 청크(10번째). 그러면 filler는
   전부 `matched_src = 1`, twin만 `matched_src = 2`다. `src = source` 행이 **정확히 5개**이고
   그 dst에 **twin이 반드시 포함**된다 — 두 대목에서 만난 문서가 더 가까운 한 대목 문서보다
   앞선다. 한 축에만 filler를 두면 다른 축 프로브의 top-10이 직교 filler로 채워져 filler도
   `matched_src = 2`가 되고 twin이 거리순으로 밀린다(1차 실행에서 실측).
4. `test_rebuild_lets_an_earlier_document_see_a_later_one` — first·second 순으로 ready하면
   `(first→second)`는 없다. `SELECT rebuild_document_edges(%s)`를 first에 호출하면 생긴다.
   두 번 호출해도 `edges_for(first)` 행 집합이 같다(멱등). `(second→first)`는 호출 전후로
   변하지 않는다.
5. `test_trigger_and_rebuild_produce_the_same_rows` — 트리거가 만든 second의 src 행 집합을
   읽고, `DELETE FROM document_edges WHERE src_document_id = second` 뒤
   `rebuild_document_edges(second)`를 호출한 결과와 `(src, dst, kind, src_chunk_index,
   dst_chunk_index, round(score::numeric, 6))` 집합으로 비교해 같아야 한다.

기존 `test_edge_kind_distinguishes_overlaps_from_related_and_never_emits_points_to`는 **픽스처를
보강**한다(단언은 그대로). 지금 픽스처는 overlap(e0·e1) + partial(e0) + decoy 10개(e1 근처)라
source의 첫 프로브(e0)에 가까운 청크가 2개뿐이고, 나머지 8자리를 직교 decoy(dist 1.0)가 채운다.
그 decoy들은 두 번째 프로브에도 들어 `matched_src = 2`가 되어 partial(`matched_src = 1`)을
상한 5 밖으로 밀어낸다(1차 실행에서 실측). decoy 루프 **뒤, source ready 전**에 `unit_vector(0)`에
`vector[3] = 0.1`을 섞은 1청크 **filler 9개**를 ready 시켜라. 그러면 첫 프로브 top-10이
overlap c0·partial·filler 8개로 차고 decoy는 두 번째 프로브에서만 만나 `matched_src = 1`이 된다.
순위는 overlap(2, 0) → partial(1, 0) → filler·decoy(1, ≈0.005)이고 partial은 상한 안에 남는다.
주석에 왜 filler가 필요한지 한 줄 남겨라.

### 2) 구현 — `backend/migrations/014_edges_triggers.sql`

기존 008 파일은 **수정하지 않는다** (이미 적용된 DB에서 다시 실행되지 않는다). 새 파일에서:

```sql
-- 014_edges_triggers.sql — 관계 판정을 rebuild_document_edges로 옮긴다 (ADR-029 개정, #94)

CREATE FUNCTION rebuild_document_edges(target_document_id uuid) RETURNS void
  LANGUAGE plpgsql
  SET hnsw.ef_search = 200
  SET random_page_cost = 1.1
  SET enable_seqscan = off
AS $$ ... $$;

CREATE OR REPLACE FUNCTION build_document_edges() RETURNS trigger
  LANGUAGE plpgsql
AS $$
BEGIN
  PERFORM rebuild_document_edges(NEW.id);
  RETURN NEW;
END; $$;
```

`CREATE OR REPLACE`는 정의 전체를 바꾸므로 트리거 함수의 `SET` 절이 사라진다 — 의도한 것이다.
트리거 `trg_build_document_edges`는 같은 함수 이름을 가리키므로 재생성하지 않는다.

`rebuild_document_edges` 본체 — 008의 구조를 옮기되 다음이 달라진다:

- `DELETE FROM document_edges WHERE src_document_id = target_document_id;` — **dst 조건을 없앤다.**
- 청크별 상수 프로브 루프는 008 그대로(`candidate.document_id <> target_document_id`, `LIMIT 10`,
  JSONB 누적). 임시 테이블·영속 중간 테이블 금지(ADR-022).
- `pair_stats`에 `count(DISTINCT dst_chunk_index) AS matched_dst`를 더한다.
- dst 청크 수는 `document_chunks`를 `document_id`로 세는 LATERAL/서브쿼리로 얻는다
  (`UNIQUE (document_id, chunk_index)` 인덱스를 탄다).
- 상한: `row_number() OVER (ORDER BY matched_src DESC, min_dist ASC, dst_document_id)` ≤ 5.
  판정(kind) **전**의 문서쌍 단위로 자른다.
- `is_overlaps = matched_src::real / src_chunks >= 0.8 AND matched_dst::real / dst_chunks >= 0.8
  AND matched_src >= 2`.
- `score`는 그대로: overlaps → `matched_src / src_chunks`, related → `1.0 - min_dist`.
- INSERT는 **`src_document_id = target_document_id`인 한 방향만.** `both_directions` CTE를 없앤다.
- 상수는 주석으로 이름을 남긴다: `NEIGHBOR_N = 10`, `OVERLAP_RATIO = 0.8`, `MIN_MATCHED = 2`,
  `MAX_NEIGHBOR_DOCUMENTS = 5`.

파일 머리 주석에 **왜 단방향인지**(남의 발견을 지우지 않는다 — 재실행 자카드 0.97→0.99),
**왜 양쪽 비율인지**(2청크 문서의 1.0 편향, C 정밀도 0.17→0.41), **왜 함수 정의 SET인지**
(OpenProxy 풀 백엔드의 generic plan — `SET LOCAL`은 `DISCARD PLANS` 뒤에야 먹었다),
**왜 조회가 양방향인지**(`search.py`·`related.py`가 이미 역방향을 UNION한다)를 008과 같은 밀도로 적는다.

### 3) `backend/tests/test_search.py`의 선행 단언 한 곳 — `test_trigger_built_edges_drive_search_expansion`

이 테스트는 edge를 손으로 넣지 않고 트리거가 만든 행만으로 검색이 확장되는지 본다. 검색 호출
**앞**의 선행 단언이 `SELECT count(*) FROM document_edges WHERE src_document_id = entry`로
"트리거가 entry의 edge를 만들었다"를 확인하는데, 단방향 저장에서는 entry가 먼저 처리되어
`(neighbor→entry)` 행만 있으므로 0이 된다(2차 실행에서 실측 — 전체 581 passed / 이 1개 failed).
선행 단언의 조건을 **`src_document_id = %s OR dst_document_id = %s`**로 바꿔 "트리거가 entry에
닿는 edge를 만들었다"로 고쳐라. 검색 결과 단언(entry 직접 히트 → neighbor가 `via.from_document_id
== entry`로 확장)은 **그대로** — 역방향 행을 step 0의 대칭 읽기가 소비하는지가 이 테스트의 본체이며,
그 단언은 지금도 통과해야 한다. docstring의 "008 트리거"는 "트리거(014)"로 바꾼다. 이 파일에서
바꾸는 것은 이 테스트 하나뿐이다.

### 4) `build_document_edges`의 기존 정의를 남기지 마라

008의 함수는 `CREATE OR REPLACE`로 덮인다. 008 파일 자체에 손대지 않는다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/python -m pytest tests/test_triggers.py tests/test_tables.py tests/test_migrations.py -q
cd backend && .venv/bin/python -m pytest -q          # 전체 — 조회 측은 step 0·1에서 이미 양방향으로 읽으므로 통과해야 한다
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. `pytest -q`가 통과해야 한다 — 조회 측(`search.py`·`related.py`)은
   step 0·1에서 양방향 읽기가 끝났으므로, 조회 테스트가 깨진다면 단방향 저장이 아니라 다른 것을
   건드린 것이다. `test_triggers.py`가 깨진다면 위 「픽스처 원칙」부터 확인하라.
2. 아키텍처 체크리스트:
   - 새 마이그레이션은 `backend/migrations/014_edges_triggers.sql` 하나뿐인가? (다른 번호·이름 금지)
   - 애플리케이션 코드(`backend/app/**`)를 **한 줄도** 바꾸지 않았는가? 테스트 변경은 `test_triggers.py`와
     `test_search.py`의 선행 단언 한 곳뿐인가?
   - `rebuild_document_edges`의 `proconfig`에 세 값이 전부 있는가?
   - 임시 테이블·`LISTEN`·`SET LOCAL`을 쓰지 않았는가?
3. 결과에 따라 `phases/m13-relation-rules/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (함수 이름·상한·저장 방향을 담아라 — 다음 step이 읽는다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `search.py`·`related.py`·`clusters.py`·`diagnostics.py`를 고치지 마라. 이유: 조회 측 양방향 읽기는
  step 0·1에서 끝났다. 이 step은 마이그레이션 파일 하나와 `test_triggers.py`, 그리고 `test_search.py`의
  선행 단언 한 곳(작업 3)만 바꾼다.
- 역방향(이웃 문서 기준) 재계산을 트리거에 넣지 마라. 이유: 비용이 이웃 청크 수에 비례해
  `finalize_job`이 5배 길어지고, kNN 비대칭이라 그래도 완전하지 않다. 전량 재계산(step 5)이 답이다.
- 픽스처를 맞추려고 정렬 키·`LIMIT 10`·비율 0.8을 바꾸지 마라. 이유: 위 「닫힌 결정」은 실 코퍼스
  시뮬레이션으로 확정한 값이다. 작은 픽스처가 깨지면 filler를 채우는 쪽이 맞다.
- 거리 임계(`min_dist < x`)를 추가하지 마라. 이유: §15·#93에서 정오가 같은 거리대에 섞여 있어
  자를 것이 없었다.
- `SET LOCAL`을 함수 안에서 쓰지 마라. 이유: OpenProxy 풀 백엔드에 generic plan이 남아 다음
  호출에 안 먹는다.
- 008 파일을 수정하지 마라. 이유: 적용된 마이그레이션은 다시 실행되지 않는다.
- 테스트를 통과시키려고 단언을 약화하거나 skip하지 마라.
- 기존 테스트를 깨뜨리지 마라.
