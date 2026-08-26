# Step 0: louvain-clusters

## 배경 — 묶는 축을 태그에서 관계 그래프로 바꾼다

`GET /api/clusters`(「주제 덩어리」)는 지금 `backend/app/services/clusters.py`의
`_assign_clusters`가 **문서마다 저장소에서 가장 많이 쓰인 태그**를 골라 묶는다. 태그가 없는
문서는 전부 "미분류"다. 즉 "문서만 올리면 알아서 정리된다"는 목표에서 정리의 축이 아직
**사람이 단 태그**다.

한편 `document_edges`에는 임베딩 완료 시점에 DB 트리거가 만든 문서↔문서 관계(`overlaps`·
`related`, ADR-029)가 이미 있다. 이 step은 묶는 축을 **관계 그래프 위의 Louvain 군집**으로
바꾼다. 태그는 군집의 **이름**을 짓는 데만 쓴다.

같은 일을 LLM 없이 하는 공개 구현(txtai Semantic Graph: kNN edge + Louvain + 키워드 라벨)의
**방법만** 가져온다. 라이브러리는 가져오지 않는다 — 그래프가 앱 메모리로 나가면 관계가 문서와
같은 트랜잭션 안에 있다는 코어 계약(ADR-015·029)이 깨진다.

### 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라 (2026-08-26 사용자 확정, #85)

| # | 결정 | 근거 |
|---|---|---|
| D1 | **조회 시점 계산.** 저장하지 않는다. 테이블·마이그레이션·배치 잡 없음 | ADR-027 규칙 3·4 — 덩어리 크기는 열람 범위 안에서만 세고, 전역 집계가 다른 사용자의 화면에 영향을 주면 안 된다. 전 문서(private 포함)로 계산해 저장하면 내가 볼 수 있는 두 문서가 **내가 못 보는 문서 때문에** 같은 덩어리에 묶인다. 열람 가능한 부분 그래프 위에서 요청마다 계산하면 이 문제가 없다. 실측 66문서 수 ms |
| D2 | **Louvain, `networkx`** (`networkx.community.louvain_communities`), `resolution=1.0`, `seed` 고정, 입력 정렬 | `networkx`는 순수 Python·BSD. 지금 개발 venv에 있는 것은 torch(`[local]`)가 끌고 온 것이라 **기본 의존성에 추가해야** clean install(`[dev]`)에서 돈다 |
| D3 | **무가중** — 문서쌍당 edge 1개 | ADR-029 결정 4: `overlaps`(매칭 비율)와 `related`(1−거리)는 척도가 달라 한 축에 섞지 않는다. 실측에서 무가중도 일관된 분할(가중 대비 문서쌍 일치 0.86) |
| D4 | **이름 = 군집 안 최다 태그**(동률 → 이름순). 태그가 하나도 없으면 **군집 안에서 연결(차수)이 가장 많은 문서의 제목**(동률 → 제목순). 이름이 겹치면 두 번째부터 ` (2)`, ` (3)` | 토크나이저 없이 된다. 키워드 라벨(c-TF-IDF)은 후속 이슈 |
| D5 | **열람 가능한 edge가 하나도 없는 문서 → "미분류"**(태그 유무와 무관). 군집이 `MAX_CLUSTERS=20`개를 넘으면 작은 것부터 "기타" | 현재 버킷·상한 로직 유지 |
| D6 | API 응답 형태 **변경 없음** — `ClusterItem{name,size,documents}`·`ClusterConnectionItem{source,target,count}` 그대로 | 프론트 타입·화면 계약을 건드리지 않는다 |

### 알고 시작할 실측 (2026-08-26, 개발 DB 실 BGE-M3 edge, 문서 66 · 무방향 edge 646)

- Louvain `resolution=1.0` → 군집 4개(25·20·16·5), 태그를 전혀 안 보고도 태그 분포와 일치하는 갈래.
- **결과는 seed·가중치·입력 순서에 따라 흔들린다** — 무가중 seed 42 vs 7의 문서쌍 일치 0.84, seed 42 vs 2026은 0.94. Louvain은 "하나의 옳은 답"이 아니라 "그럴듯한 분할 중 하나"다. 그래서 **결정론은 입력 정렬 + seed 고정으로 확보**하고, 화면 어휘는 단정하지 않는다(step 1·2).

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `CLAUDE.md` — CRITICAL 규칙. 특히 "볼 수 없는 문서는 존재하지 않는 것처럼 보인다"
- `docs/ADR.md` **ADR-027**(관계 그래프의 권한 규칙 — 규칙 3·4) · **ADR-029**(관계를 저장 시점에 만든다 — 결정 4·5) · **ADR-018**
- `docs/UI_GUIDE.md` 「관계 종류 어휘」 절 — `overlaps`를 "같은 내용"이라 부르지 않는 이유
- `backend/app/services/clusters.py` — **교체 대상.** `VISIBLE_DOCUMENTS_SQL`·`VISIBLE_EDGES_SQL`·데이터클래스·연결 집계 로직은 재사용한다
- `backend/app/services/visibility.py` — `VISIBLE_TO_USER` 술어
- `backend/tests/test_clusters.py` — **재작성 대상.** `_insert_edge`·`_insert_bidirectional_edge` 헬퍼는 그대로 쓴다
- `backend/tests/conftest.py` — `insert_test_document`·`login_as`·`db_client`·`migrated_db`
- `backend/migrations/006_edges_tables.sql` · `008_edges_triggers.sql` — edge가 양방향 두 행으로 저장되는 이유
- `backend/app/api/clusters.py` · `backend/app/api/schemas.py`(`ClusterItem` 등) — **변경하지 않는다**
- `backend/pyproject.toml` · `backend/.gitignore`

## 작업

### 1) 의존성 — `backend/pyproject.toml`

`dependencies`에 `"networkx"`를 추가한다. 주석으로 남길 것: *Louvain 군집(`services/clusters.py`).
순수 Python·BSD. torch가 끌고 오던 것을 기본 의존성으로 승격 — `[local]` 없이도 덩어리가
계산되어야 한다.* 추가 후 `.venv/bin/pip install -e ".[dev]"`로 재설치한다.

### 2) `backend/.gitignore`에 `build/` 한 줄

이유: `pip install -e`가 setuptools 산출물 `backend/build/`를 남기는데(지금 작업 트리에
실제로 untracked로 있다) 하네스가 `git add -A`로 커밋하므로 그대로 쓸려 들어간다. 이 step의
pip 재설치가 그것을 다시 만들기도 한다.

### 3) 테스트를 먼저 쓴다 — `backend/tests/test_clusters.py` 재작성

기존 파일의 태그 기반 단언은 이 step에서 거짓이 된다. 아래를 **전부** 담아라. 문서는
`insert_test_document`로, 관계는 `_insert_bidirectional_edge`로 만든다 (트리거는 FakeProvider
벡터로 임의의 edge를 만들므로, 군집 테스트는 워커를 돌리지 말고 edge를 직접 넣는다).

1. `test_clusters_require_login` — 유지.
2. `test_connected_documents_form_one_cluster_named_by_top_tag` — 서로 전부 이어진 문서 3개
   (태그 `[검색, 공통]`·`[검색]`·`[데이터베이스]`) → 덩어리 1개, 이름 `검색`, size 3, connections `[]`.
3. `test_two_dense_groups_joined_by_one_edge_become_two_clusters_with_one_connection` —
   삼각형 A(태그 `검색`) + 삼각형 B(태그 `데이터베이스`) + 둘을 잇는 edge 1개 → 덩어리 2개
   (`검색` 3 · `데이터베이스` 3), connections가 정확히 1건이고 `count == 1`.
4. `test_documents_without_visible_edges_go_to_uncategorized` — edge 없는 문서는 **태그가
   있어도** `미분류`. 태그 있는 것 1 + 없는 것 1 → `미분류` size 2.
5. `test_private_documents_do_not_shape_other_users_clusters` (ADR-027) — public 삼각형 A·B가
   **alice의 private 문서 P를 통해서만** 이어진다(P↔A의 한 문서, P↔B의 한 문서). bob으로
   조회하면 덩어리 2개·size 합 6·connections `[]`(다리가 보이지 않으므로) 이고 P가 어디에도
   세어지지 않는다. alice로 조회하면 size 합 7.
6. `test_cluster_count_is_capped_and_small_clusters_are_merged_into_other` — 서로 이어진
   문서쌍 22개(쌍마다 고유 태그) → 덩어리 22개가 상한 20에 걸려 `기타`로 접힌다. 결과 20개,
   size 합 44, `기타` size 6.
7. `test_reserved_bucket_names_do_not_merge_with_same_named_tags` — 태그 `미분류`로 이어진
   쌍 → `미분류 (태그)` size 2. edge 없는 문서 → `미분류` size 1. 둘은 다른 덩어리다.
8. `test_duplicate_top_tags_get_numbered_names` — 최다 태그가 둘 다 `공통`인 삼각형과 쌍 →
   `공통`(3)·`공통 (2)`(2).
9. `test_untagged_community_is_named_after_its_best_connected_document` — 태그 없는 문서
   4개: 삼각형 + 한 꼭짓점에 하나 더(별 모양) → 차수 3인 문서의 제목이 덩어리 이름.
10. `test_result_is_deterministic_across_calls` — 삼각형 3개를 다리로 이은 그래프를 두 번
    조회해 응답 JSON이 완전히 같다.

### 4) 구현 — `backend/app/services/clusters.py`

공개 시그니처는 **그대로**: `async def get_clusters(conn, *, user_id) -> ClusterResult`와
`ClusterDocument`·`Cluster`·`ClusterConnection`·`ClusterResult`.

교체할 내부:

```python
LOUVAIN_SEED = 42          # 결과가 seed에 따라 달라진다(문서쌍 일치 0.84~1.0). 고정한다
LOUVAIN_RESOLUTION = 1.0   # 실측(66문서)에서 4개. 1.5는 8개로 잘게 쪼갠다

def _assign_communities(
    documents: list[tuple[UUID, str, list[str]]],   # (id, title, tags) — id 순 정렬돼 들어온다
    pairs: set[tuple[UUID, UUID]],                   # 열람 가능한 문서쌍, (작은 id, 큰 id)
) -> dict[UUID, ClusterKey]: ...
```

지켜야 할 규칙:

- **무방향 그래프, 무가중.** `VISIBLE_EDGES_SQL`의 양방향 두 행을 문서쌍 하나로 접는다
  (기존 연결 집계가 이미 그렇게 한다). `kind`·`score`는 읽지 않는다.
- **결정론.** 노드는 `id` 순, edge는 `(src, dst)` 순으로 정렬해 넣고
  `louvain_communities(G, weight=None, resolution=LOUVAIN_RESOLUTION, seed=LOUVAIN_SEED)`를 쓴다.
  같은 입력에 같은 출력이 나와야 한다(테스트 10).
- **edge가 없는 문서는 Louvain에 넣지 않고** `(BUCKET, UNCATEGORIZED)`로 보낸다 (D5).
- **이름 규칙**은 D4 그대로. 예약 이름(`미분류`·`기타`)과 겹치는 태그는 기존처럼
  `(TAGGED, name)`으로 두어 `_display_name`이 ` (태그)`를 붙이게 한다. 번호는 size 내림차순,
  동률이면 덩어리 안 첫 문서 제목순으로 매긴다.
- **상한**은 기존 `MAX_CLUSTERS` 로직을 군집 단위로 재사용한다.
- **연결(`connections`)** 집계·정렬은 기존 코드를 재사용한다.
- 덩어리는 `(-size, name)`, 덩어리 안 문서는 `(title, id)` 순.
- **저장하지 않는다.** INSERT/UPDATE 없음. 마이그레이션 없음.
- `backend/app/services/diagnostics.py`의 "미분류"(태그 없는 문서)는 **다른 개념**이다 — 건드리지 않는다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/pip install -e ".[dev]" -q
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest tests/test_clusters.py -q
cd backend && .venv/bin/pytest -q
grep -n '"networkx"' backend/pyproject.toml
grep -n '^build/$' backend/.gitignore
test -z "$(git -C backend status --short --untracked-files=all build 2>/dev/null)"   # build/가 더 이상 untracked로 잡히지 않는다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가? (비즈니스 로직은 `services/`, 라우터는 재사용만)
   - ADR 기술 스택을 벗어나지 않았는가? (`networkx` 외 새 의존성 없음)
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가? — 열람 범위(`VISIBLE_TO_USER`)가 문서·edge 양쪽 SQL에 그대로 걸려 있는가
3. 결과에 따라 `phases/m12-communities/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (다음 step이 알아야 할 것: 응답 형태 불변, 이름 규칙, 상수 이름)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 군집을 테이블에 저장하거나 마이그레이션을 추가하지 마라. 이유: D1 — ADR-027 규칙 4 위반이고 범위가 3배가 된다.
- `kind`·`score`를 가중치로 쓰지 마라. 이유: D3 — 두 kind의 척도가 다르다(ADR-029 결정 4).
- 한국어 토크나이저·형태소 분석기 등 새 의존성을 넣지 마라. 이유: D4 — 이름은 태그·제목으로 짓는다. 키워드 라벨은 후속 이슈다.
- `api/schemas.py`·`api/clusters.py`·프론트 타입을 바꾸지 마라. 이유: D6 — 이 step은 `search` 스코프 하나다.
- `services/diagnostics.py`를 건드리지 마라. 이유: 그쪽 "미분류"는 태그 없는 문서라는 다른 개념이다.
- 테스트에서 워커를 돌려 edge를 만들지 마라. 이유: FakeProvider 벡터가 만드는 edge는 임의라 군집 단언이 불안정해진다. `_insert_bidirectional_edge`로 직접 넣어라.
- 실패하는 테스트를 skip·삭제하거나 단언을 약화하지 마라.
- `git commit`을 직접 하지 마라. 이유: 커밋은 execute.py가 한다.
- 기존 테스트를 깨뜨리지 마라.
