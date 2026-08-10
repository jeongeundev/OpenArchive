# Step 7: related-on-edges

## 배경 — 관련 문서가 "비슷함" 하나에서 종류로 갈린다

지금 `find_related`는 대상 문서의 **청크 평균**(`avg(embedding)`)에 가까운 문서를 점수순으로
준다. #29가 지적한 손실이 정확히 여기다 — **평균은 전반 동일·지점 연결·포괄 상세를 전부
뭉갠다.** 문서 하나가 다른 문서와 *어떻게* 관련되는지가 사라지고 숫자 하나만 남는다.

step 6이 그 구분을 이미 저장해 뒀다. 이 step은 **읽는 쪽을 옮긴다.**

```
지금                          바뀌면
avg(embedding) 계산            document_edges 조회
→ 벡터 정렬 + 과다조회         → kind별 그룹
→ DISTINCT ON 문서당 1건       → score 정렬
→ 점수순 k개                   → k개
```

**ADR-018 개정이 step 3에서 이미 기록됐다.** 이 step은 그 기록대로 코드를 바꾼다.

## 읽어야 할 파일

- `docs/ADR.md` **ADR-018 개정분** · **ADR-029 결정 5** — 왜 바꾸는지
- `backend/app/services/related.py` — **전부**. `_NEIGHBOR_CTE`·`CANDIDATE_MULTIPLIER`·
  `RELATED_SQL`·`TAG_SUGGESTION_SQL`이 이 step에서 사라지거나 바뀐다
- `backend/app/services/visibility.py` — step 4가 만든 `VISIBLE_TO_USER`. **새 쿼리도 이걸 쓴다**
- `backend/tests/test_related.py` — `ef_search` 불변식을 단언하는 자리.
  **벡터 정렬이 사라지므로 그 단언도 사라진다**
- `backend/tests/test_visibility.py` — step 4가 네 호출부를 덮어 둔 행동 테스트.
  **이 테스트는 그대로 통과해야 한다** — 구현이 바뀌어도 권한 동작은 같아야 한다
- `backend/app/routers/` · `backend/mcp/` — `RelatedDocument`를 쓰는 곳.
  필드가 늘면 여기도 바뀐다

## 작업

### 1) 테스트를 먼저 고친다

`test_related.py`에서:

- **`ef_search` 불변식 단언을 제거한다** — `find_related`에 벡터 정렬이 없어졌으므로
  지킬 대상이 사라졌다. **`search.py`의 것은 그대로 둔다**(`test_search.py`)
- **`kind`가 결과에 실려 오는지** 단언을 더한다
- **`reason`이 세 상태로 갈리는지** 단언한다

| 상황 | `reason` |
|---|---|
| 청크가 아직 없다 | `not_indexed` |
| 청크는 있는데 edge가 없다 | **`no_edges`** (신설) |
| 정상 | `None` |

> **두 상태를 하나로 합치지 마라.** *"아직 처리 중"*과 *"처리했는데 관련이 없다"*는
> 사용자에게 전혀 다른 말이고, 화면 문구도 달라야 한다(step 9).

`test_visibility.py`는 **한 줄도 고치지 마라.** 그 테스트가 통과하는 것이 이 교체가
권한을 깨지 않았다는 증거다.

### 2) `find_related`를 바꾼다

- `document_edges`에서 `src_document_id = %(id)s`인 행을 읽는다
- **권한은 `dst` 문서에 건다** — `VISIBLE_TO_USER`를 그대로 쓴다.
  볼 수 없는 문서는 결과에 없을 뿐 아니라 **자리도 남기지 않는다**(ADR-027)
- `RelatedDocument`에 **`kind`**를 더한다. `score`도 그대로 싣되,
  **척도가 `kind`마다 다르므로 종류를 섞어 정렬하지 마라** — `kind`로 묶은 뒤
  그룹 안에서 정렬한다
- `identical`(동일 텍스트)은 **그대로 둔다.** `content_hash` 기반이라 edge와 무관하다
- `based_on_version`도 유지한다 — 청크 버전을 계속 읽는다
- **`MAX_K` 상한 검증은 남긴다.** 벡터 정렬이 없어도 상한 없는 `k`는 여전히 위험하다

### 3) `suggest_tags`를 바꾼다

이웃 문서를 edge에서 가져와 태그 빈도를 센다. 나머지 로직(현재 태그 제외, 빈도순 정렬,
`limit` 자르기)은 **그대로**다.

- 이웃 수 `NEIGHBOR_LIMIT`은 남는다 — 태그를 셀 이웃 범위이지 벡터 조회 한도가 아니다
- `CANDIDATE_MULTIPLIER`는 **사라진다**

### 4) 죽은 코드를 치운다

`_NEIGHBOR_CTE`·`CANDIDATE_MULTIPLIER`와 그로 인해 쓰이지 않게 된 import를 지운다.
**내 변경으로 고아가 된 것만 지운다** — 무관한 죽은 코드는 건드리지 않는다.

### 5) API·MCP 응답 스키마를 맞춘다

`kind`가 늘었으므로 응답 스키마와 MCP 툴 출력이 바뀐다.

> ⚠️ **`app/schemas.py`와 MCP `server.py`는 tdd-guard의 매핑 구멍이다.** 훅이 대응 테스트를
> 요구하지 않으므로 **스스로 테스트를 쓴다** — `test_related_api.py`·`test_mcp_server.py`에
> `kind`가 실제로 응답에 실리는지 단언을 더한다.

## Acceptance Criteria

```bash
cd backend

# 1) 권한 행동이 그대로인가 — 이 테스트는 한 줄도 안 고쳤어야 한다
python -m pytest tests/test_visibility.py -q
git diff --name-only | grep "tests/test_visibility.py"
#   → 출력이 없어야 한다

# 2) 관련 문서·태그 추천 테스트가 통과하는가
python -m pytest tests/test_related.py tests/test_related_api.py -q

# 3) kind가 실제로 응답까지 오는가
grep -n "kind" tests/test_related_api.py
grep -n "kind" tests/test_mcp_server.py

# 4) 세 가지 reason이 단언됐는가
grep -c "no_edges" tests/test_related.py     # 1 이상
grep -c "not_indexed" tests/test_related.py  # 1 이상

# 5) 죽은 코드가 치워졌는가 — 출력이 없어야 한다
grep -nE "_NEIGHBOR_CTE|CANDIDATE_MULTIPLIER" app/services/related.py

# 6) search.py의 불변식은 살아 있는가 — 출력이 있어야 한다
grep -n "CANDIDATE_MULTIPLIER" app/services/search.py
python -m pytest tests/test_search.py -q

# 7) 새 쿼리가 권한 상수를 쓰는가
grep -n "VISIBLE_TO_USER" app/services/related.py

# 8) avg(embedding)이 사라졌는가 — 출력이 없어야 한다
grep -n "avg(embedding)" app/services/related.py

# 9) seed 데이터로 실제 응답 확인
python -m pytest -q
uvicorn app.main:app --port 8901 & sleep 3
curl -s "localhost:8901/api/documents/<seed문서id>/related" | head -40; kill %1

# 10) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. **step 6까지 적용된 DB와 seed가 필요하다.**
2. 아키텍처 체크리스트를 확인한다:
   - **`test_visibility.py`를 안 고쳤는데 통과하는가?** 고쳐야 통과한다면 권한 동작이
     바뀐 것이다 — 그건 회귀다
   - **`search.py`의 `ef_search` 불변식을 같이 지우지 않았는가?** 검색은 여전히
     벡터 정렬을 쓴다 (`CLAUDE.md` CRITICAL)
   - **`kind`를 섞어 정렬하지 않았는가?** `score`의 척도가 종류마다 다르다 (ADR-029)
   - **`no_edges`와 `not_indexed`가 갈리는가?** 합치면 사용자에게 거짓말이 된다
   - **응답이 실제로 kind별로 갈려 나오는가?** AC 9번을 눈으로 확인한다.
     전부 한 종류면 step 6의 판정을 다시 봐야 한다
3. 결과에 따라 `phases/m7-graph-relations/index.json`의 step 7을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`tests/test_visibility.py`를 수정하지 마라.** 이유: step 4가 구현과 무관하게 권한을
  고정해 둔 테스트다. 고쳐야 통과한다면 구현이 틀린 것이다
- **`search.py`의 `CANDIDATE_MULTIPLIER`·`MAX_K` 불변식을 건드리지 마라.** 이유: 검색은
  계속 벡터 정렬을 쓴다. 함께 지우면 `ef_search` 벽에 조용히 걸린다 (`CLAUDE.md` CRITICAL)
- **`identical`(동일 텍스트)을 edge로 옮기지 마라.** 이유: `content_hash` 기반이라
  벡터·관계와 무관하다. 옮기면 정확한 판정이 근사로 바뀐다
- **`kind` 없이 점수만 반환하지 마라.** 이유: 이 교체의 목적 자체가 종류를 살리는 것이다
- **`no_edges`를 빈 결과로 뭉개지 마라.** 이유: 화면이 *"아직 처리 중"*과 *"관련 없음"*을
  구분해 말해야 한다 (step 9)
- **`MAX_K` 상한 검증을 지우지 마라.** 이유: 벡터 정렬이 없어도 상한 없는 `k`는 위험하다
