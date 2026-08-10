# Step 2: measure-relation-signals

## 배경 — 이 phase의 위험이 전부 여기 몰려 있다

#38이 남긴 미측정 항목 여섯 개가 **전부 m7에서 처음 만난다.** 이 step은 그중 **지금 잴 수
있는 것**을 재서 상수를 확정하고, 나머지는 잴 수 있는 자리로 넘긴다.

**결과가 설계를 바꿀 수 있다.** 아래 「설계가 바뀌는 지점」을 반드시 확인하고, 해당하면
step 3(ADR)에 그 사실을 반영한 뒤 진행한다 — 재는 시늉만 하고 미리 정한 값을 쓰면
이 step은 없느니만 못하다.

### 정해야 할 상수 셋 (전부 데이터 분포와 무관한 성질이어야 한다)

| 상수 | 뜻 | 성질 |
|---|---|---|
| `NEIGHBOR_N` | 청크 하나당 가져올 이웃 청크 수 | 순위 컷오프. 절대 거리를 쓰지 않는다 |
| `OVERLAP_RATIO` | 매칭 청크 비율이 이 값 이상이면 `overlaps`, 미만이면 `points_to` | 비율 |
| `BROADER_MARGIN` | `word_similarity` 양방향 차가 이 값 이상이어야 방향을 인정 | **유일한 절대값** |

> **컷오프를 거리로 바꾸지 마라.** 절대 거리 임계는 시연 데이터(서로 관련이 극도로 높은
> 저장소 문서)에 맞추면 심사위원의 문서에서 그래프가 텅 비거나 헤어볼이 된다.

## 읽어야 할 파일

- `backend/app/services/search.py` — `EF_SEARCH = 200` · `MAX_K = 20` ·
  `apply_vector_search_settings`. **이 두 설정 없이 잰 값은 전부 무효다**
- `CLAUDE.md` — `MAX_K * 배수 < EF_SEARCH` 불변식과 `random_page_cost = 1.1` 규칙.
  edge 생성의 청크당 `LIMIT N`도 같은 벽에 걸린다
- `docs/OPENSQL_RESEARCH.md` **§12** — 기존 실측 기록의 형식. 새 절을 같은 형식으로 쓴다
- `docs/ADR.md` **ADR-011 보강 4·5** — `ef_search` 벽과 `random_page_cost` 실측

## 작업

### 1) 측정 스크립트를 남긴다

일회성 psql 붙여넣기로 끝내지 마라. **`scripts/measure_relations.sql`**(또는 `.py`)로 남겨야
값이 의심스러울 때 다시 돌릴 수 있고, 라이선스 만료(9/10) 전 실측 스냅샷의 일부가 된다.

모든 측정 쿼리는 **반드시** 다음 두 줄을 먼저 건다.

```sql
SET LOCAL hnsw.ef_search = 200;
SET LOCAL random_page_cost = 1.1;
```

### 2) `NEIGHBOR_N`을 정한다

청크마다 가장 가까운 이웃 N개를 가져오는 형태를 `EXPLAIN (ANALYZE, BUFFERS)`로 확인한다.

```sql
SELECT me.id, nb.id, nb.dist
  FROM document_chunks me
  CROSS JOIN LATERAL (
    SELECT c.id, c.document_id, c.embedding <=> me.embedding AS dist
      FROM document_chunks c
     WHERE c.document_id <> me.document_id
     ORDER BY c.embedding <=> me.embedding
     LIMIT %N%
  ) nb;
```

- **N을 5 · 10 · 20 · 40으로 바꿔 가며 잰다.** 확인할 것은 셋이다:
  ① `Index Scan using ...hnsw...`가 계획에 실제로 나오는가 (Seq Scan이면 값 전체가 무효)
  ② 전체 소요가 몇 초인가 — 이 쿼리가 **트리거 안에서 문서 하나 분량**으로 돌게 된다
  ③ N을 키울 때 새로 잡히는 이웃이 **다른 문서**인가, 같은 문서의 다른 청크인가
- **문서 하나 분량의 비용을 따로 잰다** — `WHERE me.document_id = <어떤 문서>`.
  이것이 step 6 트리거가 문서 하나를 처리할 때의 실제 비용이다

> 🔴 **설계가 바뀌는 지점 (a).** 문서 하나 분량이 **1초를 넘으면** 트리거 안에서 도는 것이
> `finalize_job` 트랜잭션을 그만큼 잡는다는 뜻이다. 넘으면 step 3의 ADR에 그 수치를 적고,
> 트리거를 유지할지(#24 결정 3) 재검토 대상으로 명시한다. **값을 숨기고 넘어가지 마라.**

### 3) `OVERLAP_RATIO`를 정한다

위 결과를 문서 쌍으로 집계해 **매칭 비율의 분포**를 본다.

```sql
-- 문서 쌍별: 내 청크 중 몇 개가 상대 문서를 이웃으로 잡았나 ÷ 내 청크 수
```

- 비율의 **히스토그램**(0.1 단위 버킷별 쌍 개수)을 낸다
- 눈으로 확인한다 — **`전반 동일`이라 부를 만한 쌍이 실제로 높은 비율에 몰려 있는가.**
  ADR-018과 ADR-011처럼 내용이 겹치는 쌍, `CLAUDE.md`와 개별 ADR처럼 포함 관계인 쌍을
  직접 짚어 그 값이 어디 오는지 본다
- 분포가 **이봉(두 덩어리)** 이면 골짜기를 경계로 잡는다. 한 덩어리로 뭉개져 있으면
  그 사실을 적고 0.5를 기본값으로 쓴다

> 🔴 **설계가 바뀌는 지점 (b).** 비율이 전 구간에 고르게 퍼져 `overlaps`와 `points_to`가
> 갈리지 않으면, **두 kind를 나누는 근거 자체가 데이터에 없다는 뜻**이다. 그 경우
> step 3에서 두 kind를 하나로 합칠지 결정한다 — #29의 표를 지키려고 억지로 가르지 마라.

### 4) `BROADER_MARGIN`을 정한다 — `pg_trgm` 비대칭 판정

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- 측정용. 정식 생성은 step 5다
SELECT word_similarity(a.content, b.content) AS a_in_b,
       word_similarity(b.content, a.content) AS b_in_a
  FROM ...;
```

- **`word_similarity(A, B)`는 비대칭이다** — A의 어절들이 B 안에서 얼마나 발견되는가.
  A가 상세, B가 포괄이면 `a_in_b`가 크다
- 정답을 아는 쌍으로 확인한다: **`CLAUDE.md` ↔ 개별 ADR**(CLAUDE.md가 포괄),
  **ADR 본문 ↔ 그것을 요약한 `ARCHITECTURE` 절**
- 양방향 차(`|a_in_b - b_in_a|`)가 **정답 쌍에서 얼마나 벌어지는지**, 무관한 쌍에서
  얼마나 붙는지 본다. 그 사이를 `BROADER_MARGIN`으로 잡는다
- **판정 실패 시 `related`로 떨어뜨린다**(#35). 마진 미달이 몇 %인지도 기록한다

> 🔴 **설계가 바뀌는 지점 (c).** 정답 쌍에서도 차가 안 벌어지면 `broader` 판정이 성립하지
> 않는다. 그 경우 **`broader`를 m7에서 빼고 전부 `related`로 두는 것**이 정직하다 —
> step 3에서 결정하고, #29의 관계 3종이 2종으로 줄었음을 ADR에 적는다.
>
> ⚠️ `word_similarity`는 **어절 단위 trigram**이라 한국어 조사에 영향을 받는다.
> 값이 이상하면 조사 때문인지 먼저 확인하라 — #29가 `tsvector`를 버린 것과 같은 원인이다.

### 5) 못 재는 것을 못 잰다고 적는다

아래는 이 step에서 **잴 수 없다.** 각각 어느 step이 재는지 명시해 넘긴다.

| 미측정 | 왜 지금 못 재나 | 재는 자리 |
|---|---|---|
| 재귀 CTE에서 HNSW·인덱스를 타는가 | `document_edges`가 아직 없다 | **step 8** |
| 권한 필터를 재귀항에 넣었을 때의 계획 변화 | 위와 같다 | **step 8** |
| 조회 시점 제목 resolve 조인 비용 | 위키링크가 m7 범위 밖이다 | **m9** |

### 6) `docs/OPENSQL_RESEARCH.md`에 절을 신설한다

**`## 14. 관계 판정 신호 실측 [실측 2026-08-1X]`** — §13 다음이다. 담을 것:

- 측정 환경(문서 수 · 청크 수 · `count(DISTINCT embedding::text)`)
- N별 계획과 소요 표, **문서 하나 분량의 비용**
- 비율 히스토그램과 고른 경계, 근거로 짚은 실제 문서 쌍 이름
- `word_similarity` 양방향 값 표와 고른 마진
- **확정한 상수 셋과 그 값**
- 설계가 바뀌는 지점 (a)(b)(c) 중 **실제로 걸린 것과 그 조치**

## Acceptance Criteria

```bash
# 1) 측정 스크립트가 남았는가
test -f scripts/measure_relations.sql || test -f scripts/measure_relations.py

# 2) 새 절이 생겼는가
grep -n "^## 14\." docs/OPENSQL_RESEARCH.md

# 3) 상수 셋이 값과 함께 확정됐는가 — 이름만으로는 통과할 수 없다
sed -nE '/^## 14\./,/^(## 15\.|---)$/p' docs/OPENSQL_RESEARCH.md | grep -nE "NEIGHBOR_N"
sed -nE '/^## 14\./,/^(## 15\.|---)$/p' docs/OPENSQL_RESEARCH.md | grep -nE "OVERLAP_RATIO"
sed -nE '/^## 14\./,/^(## 15\.|---)$/p' docs/OPENSQL_RESEARCH.md | grep -nE "BROADER_MARGIN"

# 4) 실제로 잰 흔적이 있는가 — 계획 문자열과 측정 환경
sed -nE '/^## 14\./,/^(## 15\.|---)$/p' docs/OPENSQL_RESEARCH.md | grep -niE "hnsw"
sed -nE '/^## 14\./,/^(## 15\.|---)$/p' docs/OPENSQL_RESEARCH.md | grep -nE "ms|초"
sed -nE '/^## 14\./,/^(## 15\.|---)$/p' docs/OPENSQL_RESEARCH.md | grep -n "DISTINCT embedding"

# 5) 못 잰 것 셋이 넘길 자리와 함께 적혔는가
sed -nE '/^## 14\./,/^(## 15\.|---)$/p' docs/OPENSQL_RESEARCH.md | grep -nE "재귀|WITH RECURSIVE"

# 6) 마이그레이션을 만들지 않았는가 — 출력이 없어야 한다
git status --porcelain backend/migrations/ | grep -E "^\?\?|^ M"

# 7) 코드를 고치지 않았는가 — 출력이 없어야 한다
git diff --name-only | grep -E "^backend/app/|^frontend/"

# 8) 전체 검증
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. **step 1의 seed가 적재된 상태여야 한다.**
2. 아키텍처 체크리스트를 확인한다:
   - **모든 측정 쿼리에 `ef_search`와 `random_page_cost`를 짝으로 걸었는가?**
     하나만 빠져도 Seq Scan으로 떨어져 값이 전부 무효다 (ADR-011 보강 5)
   - **계획에 HNSW Index Scan이 실제로 찍혔는가?** 찍히지 않았다면 값을 적기 전에
     원인을 밝혀라 — 그것 자체가 이 step의 결과다
   - **`N` 값이 `EF_SEARCH`보다 충분히 작은가?** 등호에서도 모자란다 (ADR-011 보강 4)
   - **설계가 바뀌는 지점 (a)(b)(c)를 실제로 판정했는가?** 걸린 것이 없다면
     "셋 다 걸리지 않았다"를 명시적으로 적어라. 침묵은 확인과 다르다
   - **상수가 데이터 분포에 의존하는 형태로 정해지지 않았는가?** `dist < 0.25` 같은 절대
     거리가 상수 목록에 들어왔다면 결정 6을 어긴 것이다
3. 결과에 따라 `phases/m7-graph-relations/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **마이그레이션 파일을 만들지 마라.** 이유: 테이블은 step 5, 트리거는 step 6이다.
  이 step에서 만든 `pg_trgm`은 **측정용**이며 정식 생성은 step 5가 한다
- **애플리케이션 코드를 고치지 마라.** 이유: 이 step의 산출물은 값과 기록이다
- **미리 정한 값을 쓰고 측정한 척하지 마라.** 이유: 이 phase의 위험이 전부 여기 있고,
  틀린 상수는 step 6 이후 전부를 조용히 망가뜨린다
- **절대 거리 임계를 상수로 도입하지 마라.** 이유: #24 결정 6이 순위 기반으로 정했다.
  데이터가 바뀌면 그래프가 텅 비거나 헤어볼이 된다
- **임시 테이블을 쓰지 마라.** 이유: OpenProxy가 `DISCARD ALL`을 하지 않아 다음 클라이언트로
  누수된다 (ADR-022). 중간 결과는 CTE로 처리한다
- **합성 벡터로 재지 마라.** 이유: step 1이 실데이터를 넣은 이유가 이것이다
