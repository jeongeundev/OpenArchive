# Step 8: graph-search

## 배경 — 여기서 검색이 달라진다

지금 검색은 질의 벡터에 가까운 청크를 문서당 1건으로 줄여 나열한다. **거기서 끝난다.**
이 step은 그 진입점에서 **관계를 타고 확장**한다.

```
"정합성" 검색
  진입점 ─ 문서A · 3번 청크          ← 직접 매칭 (여기까지가 지금)
     │
     ├─ 이어짐    문서A · 2,4번 청크   앞뒤 맥락 복원      ← 조회 시점 (chunk_index ± 1)
     ├─ 지점연결  문서B · 7번 청크     다른 문서인데 이 대목에서 만난다
     ├─ 포괄      문서C               개요 문서
     └─ 개정 전   문서A v2 · 3번      예전 서술            ← 조회 시점 (document_versions)
```

**남은 미측정 둘이 이 step에서 처음 만난다** — step 2가 잴 수 없어 넘긴 것들이다.
작업 순서를 **측정 → 설계 확정 → 구현**으로 잡는다.

## 읽어야 할 파일

- `backend/app/services/search.py` — `SEARCH_SQL`의 골격과 `apply_vector_search_settings`.
  **진입점 부분은 이 쿼리 그대로다**
- `backend/app/services/visibility.py` — `VISIBLE_TO_USER`. **재귀항에도 건다**
- `docs/ADR.md` **ADR-027**(순회가 private에서 멈춘다) · **ADR-011 보강 2**
  (`DISTINCT ON`의 위치) · **ADR-029**
- `docs/OPENSQL_RESEARCH.md` **§14** — step 2가 "step 8이 잰다"고 넘긴 항목
- `backend/migrations/002_tables.sql` — `document_versions`의 키. 개정 관계를 조회 시점에 만든다

## 작업

### 1) 먼저 잰다 — 설계보다 측정이 앞이다

`document_edges`가 이제 있으므로 step 2가 못 잰 둘을 잰다. **`EXPLAIN (ANALYZE, BUFFERS)`**로
확인하고 결과를 `docs/OPENSQL_RESEARCH.md` **§14에 덧붙인다**(새 절을 만들지 마라).

| 재는 것 | 확인할 것 |
|---|---|
| **재귀 CTE가 인덱스를 타는가** | 재귀항에서 `(src_document_id, kind)` 인덱스가 실제로 쓰이는가. `random_page_cost = 1.1`이 재귀항에도 먹는가 |
| **권한 필터를 재귀항에 넣었을 때의 계획 변화** | 필터를 밖으로 뺀 형태와 비교. **밖으로 빼면 안 되지만**(아래), 비용 차이를 기록한다 |

> 🔴 **설계가 바뀌는 지점.** 재귀항이 인덱스를 못 타서 깊이 2가 실용적이지 않으면
> **깊이를 1로 줄인다.** 줄인 사실과 근거를 §14에 적어라 — 조용히 깊이만 낮추면
> 다음 사람이 "왜 1인가"를 알 수 없다.

### 2) 순회 규칙을 정확히 지킨다

- **깊이 상한을 반드시 둔다.** 상한 없는 재귀는 사이클에서 돌고, edge가 대칭이라
  `A → B → A`가 **항상 존재한다**
- **방문한 노드를 다시 담지 않는다.** PostgreSQL 14+의 `CYCLE` 절을 쓰거나 경로 배열로 막는다
- **권한은 재귀항 안에 있어야 한다.** 밖에서 거르면 **private 노드를 경유해** 그 너머
  문서가 결과에 들어온다 — ADR-027 결정 1이 막는 것이 정확히 이것이다.
  *"순회가 통과하지 못한다"*는 목록에서 빠지는 것과 다르다
- **진입점 벡터 검색과 확장을 한 쿼리로 묶는다** (`CLAUDE.md` CRITICAL —
  DB에서 넓게 가져와 앱에서 후처리하지 마라)
- **`DISTINCT ON`은 확장이 끝난 뒤에 적용한다.** 벡터 정렬 → `LIMIT` → 확장 → 중복 제거 →
  최종 정렬 순서다. `DISTINCT ON` 직후에 `LIMIT`을 붙이면 유사도가 아니라
  `document_id`(UUID) 순으로 잘린다 (ADR-011 보강 2)
- **`SET LOCAL random_page_cost = 1.1`과 `hnsw.ef_search`를 건다.** 진입점이 벡터 검색이다
- **plain `BEGIN … COMMIT`** 안에서 실행한다. `BEGIN READ ONLY`는 OpenProxy가 Replica로
  라우팅해 방금 임베딩된 청크가 누락된다 (ADR-010)

### 3) 저장하지 않는 관계를 조회 시점에 붙인다

- **이어짐**: 진입점 청크의 `chunk_index ± 1`. **깊이 1에서만** 필요하다 —
  순회 대상이 아니라 진입점 맥락 복원이다
- **개정**: `document_versions`에서 직전 버전. 진입점 문서에만 붙인다

> 분류(태그 공유)·선후(생성 순서)는 **순회에 넣지 마라.** 태그 하나 겹치는 문서 전부가
> 이웃이 되어 깊이 2에서 사실상 전체 문서가 된다 (ADR-029 결정 2).

### 4) 결과 구조

`SearchHit`에 **어떻게 도달했는지**를 싣는다. 기존 필드는 그대로 두고 더한다.

- `via` — 직접 매칭이면 비어 있고, 확장이면 `(어느 문서에서, 어떤 kind로, 깊이 몇)`
- **직접 매칭이 항상 위**다. 확장 결과가 진입점을 밀어내면 검색이 나빠진 것으로 보인다

### 5) 테스트

**행동으로 고정한다.**

- 관계로 이어진 문서가 **질의에 직접 매칭되지 않아도** 결과에 나오는가
- **private 노드를 경유해 그 너머 문서가 새지 않는가** — step 1의 seed가 이 배치를
  이미 만들어 뒀다(참조되는 private 문서). **이 테스트가 이 step의 핵심이다**
- 깊이 상한이 지켜지는가
- 사이클에서 무한 순회하지 않는가
- 직접 매칭이 확장보다 위에 오는가
- `via`가 실제 관계와 일치하는가

`tests/test_visibility.py`에 **그래프 검색 호출부를 더한다** — 세 시선 × 새 호출부.

## Acceptance Criteria

```bash
cd backend

# 1) 테스트가 있고 통과하는가
python -m pytest tests/test_search.py tests/test_visibility.py -q

# 2) 순회 권한 테스트가 실제로 있는가 — 경유 차단이 핵심이다
grep -nE "경유|traverse|through" tests/test_visibility.py

# 3) 측정이 §14에 덧붙었는가 — 새 절을 만들지 않았어야 한다
sed -n '/^## 14\./,/^## 15\.\|^---$/p' docs/OPENSQL_RESEARCH.md | grep -nE "RECURSIVE|재귀"
grep -c "^## 15\." docs/OPENSQL_RESEARCH.md   # 0 이어야 한다

# 4) 깊이 상한과 사이클 방지가 쿼리에 있는가
grep -nE "CYCLE|depth" app/services/search.py

# 5) 권한이 재귀항 안에 있는가 — 눈으로 확인할 것
grep -n "VISIBLE_TO_USER" app/services/search.py

# 6) 벡터 설정을 걸었는가
grep -n "apply_vector_search_settings" app/services/search.py

# 7) READ ONLY를 쓰지 않았는가 — 출력이 없어야 한다
grep -rniE "begin +read +only" app/

# 8) 앱에서 후처리 필터링하지 않는가 — 파이썬 쪽 필터가 늘지 않았는지 눈으로 확인
git diff app/services/search.py | grep -nE "^\+.*(for |if .*visibility|filter\()"

# 9) 실제 응답 확인 — 확장이 눈에 보이는가
uvicorn app.main:app --port 8902 & sleep 3
curl -s "localhost:8902/api/search?q=정합성&k=5" | head -60; kill %1

# 10) 기존 테스트 전부 통과
python -m pytest -q

# 11) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. **step 6까지 적용된 DB와 seed가 필요하다.**
2. 아키텍처 체크리스트를 확인한다:
   - **측정을 실제로 먼저 했는가?** 재귀항이 인덱스를 타는지 확인 없이 구현했다면
     깊이 2가 실용적인지 아무도 모르는 상태다
   - **권한 필터가 재귀항 *안*에 있는가?** 밖에 있으면 private을 경유해 그 너머가 샌다.
     **이것이 ADR-027 결정 1의 전부다**
   - **깊이 상한과 사이클 방지가 둘 다 있는가?** edge가 대칭이라 `A→B→A`는 항상 있다
   - **`DISTINCT ON`이 확장 뒤에 오는가?** 앞에 오면 UUID 순으로 잘린다 (ADR-011 보강 2)
   - **직접 매칭이 확장에 밀리지 않는가?** AC 9번을 눈으로 확인한다
   - **단일 SQL인가?** 앱에서 확장 결과를 다시 거르면 `CLAUDE.md` CRITICAL 위반이다
3. 결과에 따라 `phases/m7-graph-relations/index.json`의 step 8을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **권한 필터를 재귀 바깥으로 빼지 마라.** 이유: private 노드를 경유해 그 너머 문서가
  결과에 들어온다. ADR-027 결정 1이 막는 것이 정확히 이것이다
- **깊이 상한 없이 순회하지 마라.** 이유: edge가 대칭이라 사이클이 항상 존재한다
- **분류(태그 공유)·선후(생성 순서)를 순회에 넣지 마라.** 이유: 깊이 2에서 전체 문서가 된다
- **`BEGIN READ ONLY`를 쓰지 마라.** 이유: OpenProxy가 Replica로 라우팅한다 (ADR-010)
- **DB에서 넓게 가져와 앱에서 거르지 마라.** 이유: `CLAUDE.md` CRITICAL
- **임시 테이블을 쓰지 마라.** 이유: OpenProxy 누수 (ADR-022)
- **`DISTINCT ON` 직후에 `LIMIT`을 붙이지 마라.** 이유: UUID 순으로 잘린다 (ADR-011 보강 2)
- **`docs/OPENSQL_RESEARCH.md`에 새 절을 만들지 마라.** 이유: §14가 관계 측정의 자리다.
  같은 주제가 두 절로 갈리면 다음 사람이 한쪽만 읽는다
- **`🔒`나 "권한 없는 문서 N건" 표시를 넣지 마라.** 이유: 개수가 새는 것 자체가
  ADR-027 위반이다
