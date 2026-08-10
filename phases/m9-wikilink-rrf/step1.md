# Step 1: wikilink-resolve

## 배경 — 해석이 조회 시점에 일어난다

step 0이 저장한 것은 **제목 문자열**이다. 그것을 문서로 바꾸는 일은 **조회할 때, 조회하는
사람의 열람 범위에서** 일어난다.

```sql
SELECT l.target_title, d.id
  FROM document_links l
  LEFT JOIN documents d
    ON d.title = l.target_title
   AND (d.visibility = 'public' OR d.owner_id = %(user)s)   -- VISIBLE_TO_USER
 WHERE l.src_document_id = %(id)s;
```

**`LEFT JOIN`이 핵심이다.** `d.id`가 NULL이면 깨진 링크인데, 그 원인이
**"없는 문서"인지 "볼 수 없는 문서"인지 구분되지 않는다** — 그것이 정확히 목적이다
(ADR-027 결정 5).

## 읽어야 할 파일

- `docs/ADR.md` **ADR-027 결정 5** — 왜 구분되면 안 되는지
- `backend/app/services/visibility.py` — `VISIBLE_TO_USER`
- `backend/app/services/diagnostics.py` — m8 step 6. **깨진 링크 항목이 여기 추가된다**
- `backend/app/services/related.py` — 관계 조회 방식. 백링크가 비슷한 모양이다

## 작업

### 1) 테스트를 먼저 쓴다

`tests/test_visibility.py`에 **위키링크 호출부를 추가**한다 — 세 시선 × 두 호출부
(링크 해석 · 백링크).

- **private를 가리키는 링크가 익명에게 깨진 링크로 보이는가**
- **없는 문서를 가리키는 링크와 구분되지 않는가** — 응답에 사유가 실리면 안 된다
- 같은 제목의 public·private가 둘 다 있을 때, 익명에게 **public이 잡히는가**
  (저장 시점에 굳혔다면 여기서 실패한다)
- 백링크가 **열람 범위 기준**인가 — 나를 가리키는 private 문서는 안 보인다
- 열람 가능한 동명이 여럿일 때 step 0이 정한 대로 동작하는가

### 2) 해석·백링크 서비스

| 기능 | 내용 |
|---|---|
| 링크 해석 | 문서가 낸 링크 목록 + 해석 결과(문서 id 또는 깨짐) |
| 백링크 | **나를 가리키는 문서들** — `target_title = 내 제목`인 링크의 출발 문서 |

- 둘 다 `VISIBLE_TO_USER`를 쓴다
- **백링크는 출발 문서에 권한을 건다** — 내가 볼 수 없는 문서가 나를 가리켜도
  나에게는 보이지 않는다
- 깨진 링크에 **사유를 붙이지 마라.** `{title, document_id: null}` 이상을 주면 누출이다

### 3) 진단에 「깨진 링크」를 추가한다

m8 step 6이 자리를 남겨 뒀다. 같은 구조로 항목 하나를 더한다.

- **열람 범위 기준으로 센다** — 내가 볼 수 없는 문서를 가리키는 링크는
  **나에게는 깨진 링크로 세어진다.** 이것이 옳은 동작이다
- 진단 화면에 항목이 하나 느는 것이므로, m8이 만든 구조를 그대로 쓴다

### 4) 순회에 `refers`를 넣을 것인가

**넣는다면** m7 step 8의 그래프 검색 쿼리에 링크 해석을 조인해야 한다.

- **1:N일 수 있다** — 동명 문서가 여럿이면 한 링크가 여러 노드로 퍼진다
- **깨진 링크는 순회 대상이 아니다** — 갈 곳이 없다
- 비용이 는다: 순회 매 단계마다 제목 조인이 붙는다. **`EXPLAIN`으로 확인하고
  §14에 덧붙여라** — #38이 "조회 시점 제목 resolve 조인의 비용"을 미측정으로 남겼고,
  **여기가 그것을 재는 자리다**

> 🔴 **비용이 크면 순회에서 빼고 문서 상세에만 표시한다.** 뺀 사실과 측정값을 적어라.
> 위키링크는 3순위이고, 순회 성능을 해치면서까지 넣을 것은 아니다.

## Acceptance Criteria

```bash
cd backend

# 1) 테스트 통과
python -m pytest tests/test_visibility.py tests/test_diagnostics.py -q

# 2) 깨진 링크의 사유가 응답에 없는가 — 출력이 없어야 한다
grep -rnE "권한|private|forbidden" app/services/links.py 2>/dev/null | grep -i "reason"

# 3) 동명 처리가 테스트로 고정됐는가
grep -nE "동명|같은 제목|duplicate.*title" tests/test_visibility.py

# 4) 진단에 깨진 링크가 추가됐는가
grep -nE "broken|깨진" app/services/diagnostics.py

# 5) 순회 비용 측정이 §14에 덧붙었는가 (순회에 넣은 경우)
sed -n '/^## 14\./,/^## 15\.\|^---$/p' ../docs/OPENSQL_RESEARCH.md | grep -nE "resolve|제목 조인"

# 6) 실제 응답 — 두 시선에서 깨진 링크 수가 다른가
uvicorn app.main:app --port 8907 & sleep 3
curl -s localhost:8907/api/diagnostics | grep -i broken
curl -s localhost:8907/api/diagnostics -b "session=<쿠키>" | grep -i broken
kill %1

# 7) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - **깨진 링크와 권한 없음이 응답에서 구분 불가능한가?** 구분되면 ADR-027 결정 5 위반이다
   - **`LEFT JOIN`에 권한이 붙어 있는가?** 조인 조건이 아니라 `WHERE`에 있으면
     행이 통째로 사라져 링크 자체가 없어진다
   - **백링크에 출발 문서 권한이 걸렸는가?**
   - **동명 처리가 step 0의 결정대로인가?**
   - **순회에 넣었다면 비용을 실제로 쟀는가?** 안 쟀으면 넣지 마라
3. 결과에 따라 `phases/m9-wikilink-rrf/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **깨진 링크에 사유를 붙이지 마라.** 이유: "권한 없음"과 "없는 문서"가 구분되면
  private 문서의 존재가 샌다 (ADR-027 결정 5)
- **권한을 `WHERE`로 옮기지 마라.** 이유: `LEFT JOIN` 조건에 있어야 링크는 남고
  대상만 NULL이 된다. `WHERE`로 가면 링크 자체가 사라져 사용자가 자기가 쓴 링크를 잃는다
- **전역 기준으로 깨진 링크를 세지 마라.** 이유: ADR-027 결정 3
- **측정 없이 순회에 `refers`를 넣지 마라.** 이유: 순회 매 단계에 조인이 붙는다.
  3순위 기능이 1순위 성능을 해치면 안 된다
- **`document_links`에 해석 결과를 캐시하지 마라.** 이유: 조회 시점 규칙이라
  `visibility` 토글이 자동 반영되는 것이 이 설계의 이득이다
