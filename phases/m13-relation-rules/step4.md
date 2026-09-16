# Step 4: cluster-labels

## 배경 — 덩어리 이름을 공통 태그가 먹는다 (#93 G2)

「관계 지도」(`GET /api/clusters`, `backend/app/services/clusters.py`)는 관계 그래프의 Louvain
군집에 **군집 안 최빈 태그**로 이름을 붙인다(ADR-042 결정 4). 실 코퍼스 C(KOCCA 규정집)에
판본 태그 `2025판`·`2022판`을 달았더니 덩어리 이름이 「2025판」「2025판 (2)」「2025판 (3)」이
됐다 — 모든 문서가 가진 태그가 모든 덩어리의 최빈값이 된다. 같은 자리가 시연 코퍼스에서는
「경영지원」「경영지원 (2)」로 나타났다(§15 (b)).

이름은 **그 덩어리를 다른 덩어리와 구별하는** 태그여야 한다.

### 닫힌 결정

- 태그 점수 = **군집 안 빈도 − 군집 밖 빈도**(밖 = 요청자가 볼 수 있는 나머지 문서 전부, 미분류 포함).
- 점수가 **양수인 태그 중 최대**를 이름으로 쓴다. 동률은 사전순.
- 양수 태그가 없으면(모든 태그가 밖에 더 많거나 같다) **기존 폴백** — 군집 안 차수가 가장 큰 문서의 제목.
- 그 밖의 규칙(예약 이름 `미분류`·`기타` 충돌 시 ` (태그)`·` (문서)`, 같은 이름 번호 매기기, 상한 20,
  결정론)은 **그대로**.

이 규칙에서 **같은 태그 이름의 덩어리 둘은 원리상 생기지 않는다** — 한 태그가 두 군집에 있으면
점수가 `a − b`와 `b − a`라 둘 다 양수일 수 없다. 번호 매기기는 제목 라벨(동명 문서)과
태그·제목 이름 충돌에서만 남는다.

## 읽어야 할 파일

- `CLAUDE.md` — "볼 수 없는 문서는 존재하지 않는 것처럼 보인다"(밖 빈도도 열람 범위 안에서만 센다)
- `docs/ADR.md` **ADR-042**(결정 4·5) · **ADR-027**(규칙 3·4 — 열람 범위 안의 집계)
- `docs/UI_GUIDE.md` 「관계 지도」 관련 문구 — 덩어리 이름을 주제·분류로 단정하지 않는 원칙
- `backend/app/services/clusters.py` — **수정 대상.** `_assign_communities`의 라벨 부분만
- `backend/tests/test_clusters.py` — **수정·추가 대상.** 헬퍼 `_insert_document`·`_insert_edge`·`_insert_bidirectional_edge`·`_connect_all`·`_cluster_map`
- `backend/app/api/clusters.py` · `backend/app/api/schemas.py` — **변경하지 않는다**

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_clusters.py`

새 규칙에서 거짓이 되는 기존 테스트 하나를 재작성한다:

- `test_duplicate_top_tags_get_numbered_names` → `test_duplicate_fallback_titles_get_numbered_names` —
  태그 없는 문서로 삼각형 둘을 만들되 각 삼각형의 중심 문서 제목을 똑같이 `회의록`으로 둔다
  (삼각형 안 차수는 모두 2로 동률이므로, 제목 `회의록`이 사전순으로 앞서게 나머지 제목을
  `ㅎ`로 시작하는 것으로 둔다 — 동률 규칙은 제목순이다). 결과는 `회의록`(3)·`회의록 (2)`(3).
  태그 라벨로는 같은 이름이 생길 수 없다는 사실을 docstring에 적어라.

새 테스트 3개:

1. `test_a_tag_shared_across_clusters_does_not_name_them` — 삼각형 A(태그 `["2025판", "인사복무"]`
   ×3)와 삼각형 B(태그 `["2025판", "회계계약"]` ×3), 둘 사이 edge 없음. 이름은 `인사복무`·`회계계약`.
   (`2025판`은 각 군집에서 3 − 3 = 0.)
2. `test_a_cluster_whose_tags_are_all_shared_falls_back_to_its_central_document` — 삼각형 둘 다
   태그가 `["2025판"]`뿐. 두 덩어리 이름은 각각 중심 문서 제목(차수 동률이라 제목 사전순 첫 번째)이다.
   응답에 `2025판`이라는 이름의 덩어리가 없다.
3. `test_tag_score_counts_outside_documents_within_the_visible_scope_only` — alice의 private 문서
   3개(태그 `["보안"]`, 서로 연결)와 public 삼각형(태그 `["보안", "공개"]`). **bob**으로 조회하면
   public 덩어리 이름은 `보안`이다(bob에게는 밖에 `보안` 문서가 없다 — 3 − 0). **alice**로
   조회하면 public 덩어리는 `공개`(보안 3 − 3 = 0 < 공개 3 − 0)이고 private 덩어리는 `보안`.

기존 `test_connected_documents_form_one_cluster_named_by_top_tag`(태그 `[검색, 공통]`·`[검색]`·
`[데이터베이스]` 한 덩어리 → `검색`)는 새 규칙에서도 `검색` 2 − 0 = 2로 통과한다. 이름을
`..._named_by_its_distinctive_tag`로 바꾸고 docstring을 갱신하라.

### 2) 구현 — `backend/app/services/clusters.py`

`_assign_communities` 안 라벨 계산만 바꾼다. 시그니처·반환형·`ClusterKey`·`TAGGED`/`COMMUNITY`
구분·번호 매기기·예약 이름 처리·상한은 그대로.

```python
# 전체(열람 범위) 태그 빈도를 한 번 세고, 군집마다 안 빈도 − (전체 − 안) 으로 점수를 낸다.
total_tag_counts = Counter(tag for _, _, tags in documents for tag in tags)
...
score = {tag: count - (total_tag_counts[tag] - count) for tag, count in tag_counts.items()}
positive = {tag: s for tag, s in score.items() if s > 0}
if positive:
    label = min(positive, key=lambda tag: (-positive[tag], tag)); kind, origin = TAGGED, "태그"
else:
    (기존 중심 문서 제목 폴백)
```

주석에 #93 G2(「2025판」「2025판 (2)」)와 §15 (b)(「경영지원」 둘)를 근거로 남긴다.

`docs/ADR.md`·`UI_GUIDE.md`는 step 7에서 갱신하므로 여기서 문서를 고치지 않는다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/python -m pytest tests/test_clusters.py -q
cd backend && .venv/bin/python -m pytest -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 밖 빈도가 **요청자가 볼 수 있는 문서**로만 계산되는가? (전 문서 집계를 새로 조회하지 않았는가)
   - API 응답 형태(`ClusterItem`·`ClusterConnectionItem`)가 그대로인가?
   - `_assign_communities`의 결정론(정렬·seed)이 유지되는가? (`test_result_is_deterministic_across_calls`)
3. 결과에 따라 `phases/m13-relation-rules/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- Louvain 파라미터(`LOUVAIN_RESOLUTION`·`LOUVAIN_SEED`)를 바꾸지 마라. 이유: 이 step은 이름 규칙만이다. resolution은 §15에서 규모 의존이 걷히지 않아 보류됐다.
- c-TF-IDF·형태소 분석 같은 키워드 라벨을 넣지 마라. 이유: ADR-042가 기각했다(토크나이저 의존).
- 새 SQL 조회를 추가하지 마라. 이유: `VISIBLE_DOCUMENTS_SQL`이 이미 열람 범위의 문서·태그를 전부 가져온다.
- 프론트엔드를 건드리지 마라. 이유: 이름 문자열만 달라지고 계약은 같다.
- 기존 테스트를 깨뜨리지 마라 (재작성을 지시한 하나 제외).
