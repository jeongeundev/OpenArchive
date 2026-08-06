# Step 1: tag-suggestion-service

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"관련 문서·태그 추천"** 절 (세 가지 공통 규칙 + 태그 추천 쿼리 전문)
- `/docs/ADR.md` — **ADR-019**(태그 추천), **ADR-018**, **ADR-011 보강 1·4·5**, **ADR-010**
- `backend/app/services/related.py` — **이전 step에서 만든 파일.** 이 step은 여기에 함수를 추가한다
- `backend/tests/test_related.py` — 이전 step의 테스트. 픽스처와 헬퍼를 재사용하라
- `backend/app/services/search.py` — `EF_SEARCH` 상수의 출처

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 청크 존재 확인·`SET LOCAL`·권한 필터 처리를 **그대로 재사용**하라. 같은 로직을 다시 쓰지 말고 공통부를 함수로 뽑아도 좋다.

## 작업

`backend/app/services/related.py`에 태그 추천을 추가한다. **태그를 임베딩하지 않는다** — 유사 문서를 먼저 찾고 그 문서들에 달린 태그를 빈도순으로 제시한다 (ADR-019).

### 시그니처

```python
@dataclass(frozen=True)
class TagSuggestion:
    tag: str
    freq: int

@dataclass(frozen=True)
class TagSuggestionResult:
    items: list[TagSuggestion]
    based_on_version: int | None
    reason: str | None            # 청크가 없으면 "not_indexed"


async def suggest_tags(
    conn: psycopg.AsyncConnection,
    *,
    document_id: UUID,
    user_id: str | None = None,
    limit: int = 5,
) -> TagSuggestionResult: ...
```

### 처리 순서

**1) 청크 존재 여부와 기준 버전 확인** — step 0의 관련 문서와 완전히 동일하다. 청크가 0건이면 쿼리를 실행하지 않고 `items=[], reason="not_indexed"`를 반환한다. 이유: `avg(embedding)`이 NULL이면 에러 없이 무작위 결과가 나온다.

**2) 대상 문서에 이미 달린 태그를 조회해 파라미터로 넘긴다** (`documents.tags`). 추천에서 제외해야 한다.

**3) 아래 쿼리를 그대로 쓴다.**

```sql
WITH me AS (
  SELECT avg(embedding) AS v FROM document_chunks WHERE document_id = %(id)s
),
cand AS (
  SELECT c.document_id, c.embedding <=> (SELECT v FROM me) AS dist
  FROM document_chunks c       -- 관련 문서와 같은 구조 — 필터를 여기 둔다
  JOIN documents d ON d.id = c.document_id
  WHERE c.document_id <> %(id)s
    AND (d.visibility = 'public' OR d.owner_id = %(user)s)
  ORDER BY c.embedding <=> (SELECT v FROM me)
  LIMIT 100
),
best AS (
  SELECT DISTINCT ON (document_id) document_id, dist
  FROM cand
  ORDER BY document_id, dist
),
neighbors AS (                 -- ★ 유사도 순으로 자른다 (document_id 순이 아니다)
  SELECT document_id FROM best ORDER BY dist LIMIT 10
)
SELECT t.tag, count(*) AS freq
FROM neighbors n
JOIN documents d ON d.id = n.document_id
CROSS JOIN LATERAL unnest(d.tags) AS t(tag)
WHERE NOT (t.tag = ANY(%(current_tags)s::text[]))   -- 이미 달린 태그 제외
GROUP BY t.tag ORDER BY freq DESC, t.tag LIMIT %(limit)s
```

후보 `LIMIT 100`과 이웃 `LIMIT 10`은 **고정 상수**다. `limit` 파라미터는 최종 태그 개수에만 적용된다. 후보 `LIMIT`을 올리려면 `hnsw.ef_search`(200)와의 관계를 다시 확인해야 한다 (ADR-011 보강 4).

**4) 트랜잭션과 세션 설정** — step 0과 동일하다.

```python
async with conn.transaction():
    await conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
    await conn.execute("SET LOCAL random_page_cost = 1.1")
    ...
```

### 책임 경계

step 0과 같다. 대상 문서에 대한 요청자의 접근 권한은 step 2의 라우터가 확인한다. 이 함수는 **이웃 문서에 대한 권한 필터**만 책임진다 — 빠뜨리면 private 문서의 태그가 추천으로 새어나간다 (ADR-018).

## 테스트

`backend/tests/test_related.py`에 **먼저** 테스트를 추가한다. 최소한 아래를 덮어야 한다.

- 유사 문서들의 태그가 **빈도 내림차순**으로 반환된다 (동률이면 태그 이름 오름차순)
- 대상 문서에 이미 달린 태그는 결과에 없다
- **타인의 private 문서에만 달린 태그가 결과에 없다** — 이 테스트가 ADR-018의 핵심 요구다
- 청크가 0건이면 `items == []`, `reason == "not_indexed"`
- 이웃 문서가 없거나 이웃에 태그가 없으면 빈 목록을 반환한다 (에러가 아니다 — 콜드스타트는 정상 상태다)
- `limit`이 결과 개수를 제한한다

DB는 실제 컨테이너를 쓴다. Mock으로 대체하지 마라.

## Acceptance Criteria

```bash
cd backend
source .venv/bin/activate
pytest tests/test_related.py -q      # 전부 통과
pytest -q                            # 기존 테스트도 통과
ruff check .                         # 린트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m4-mcp-gateway/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (함수·데이터클래스 이름 포함)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 태그를 임베딩하지 마라. `tags` 테이블이나 태그용 벡터 컬럼을 만들지 마라. 이유: 또 하나의 동기화 대상이 생기고, 한두 단어 태그는 문장 임베딩 모델의 강점이 발휘되지 않는다 (ADR-019)
- `best`에서 곧바로 `LIMIT`을 걸지 마라. 반드시 `ORDER BY dist` 뒤에 자른다. 이유: `document_id`(UUID) 순으로 이웃이 뽑힌다 (ADR-011 보강 1)
- 권한 필터를 `cand` 밖으로 빼지 마라 (ADR-018 재개정)
- `BEGIN READ ONLY`를 쓰지 마라 (ADR-010)
- 임시 테이블을 쓰지 마라. 중간 결과는 CTE로 처리한다 (ADR-022)
- LLM으로 태그를 생성하지 마라. 이유: 이 플랫폼은 생성 LLM을 탑재하지 않는다 (ADR-015)
- step 0의 `find_related` 동작을 바꾸지 마라. 공통부를 뽑는 리팩터는 허용하되, 기존 테스트가 그대로 통과해야 한다
