# Step 0: related-service

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — 특히 **"관련 문서·태그 추천"** 절 (세 가지 공통 규칙 + 관련 문서 쿼리 전문)
- `/docs/ADR.md` — **ADR-018**(질의 시점 `avg`, 재개정 포함), **ADR-011 보강 1·4·5**, **ADR-010**, **ADR-022**
- `backend/app/services/search.py` — 이 step이 따라야 할 3단계 구조·`SET LOCAL`·`MAX_K`가 이미 구현되어 있다
- `backend/tests/test_search.py` — DB 의존 테스트를 어떻게 쓰는지의 기준
- `backend/tests/conftest.py` — `migrated_db`, `insert_test_document`, `process_all_embedding_jobs` 픽스처/헬퍼

기존 검색 서비스의 쿼리 구성과 트랜잭션 처리 방식을 꼼꼼히 읽고, 같은 규약을 따르라.

## 작업

`backend/app/services/related.py`를 새로 만든다. 문서 상세의 "관련 문서"와 "동일 텍스트 문서"를 구하는 서비스다.

### 시그니처

```python
@dataclass(frozen=True)
class RelatedDocument:
    document_id: UUID
    title: str
    tags: list[str]
    score: float          # 1 - distance

@dataclass(frozen=True)
class IdenticalDocument:
    document_id: UUID
    title: str

@dataclass(frozen=True)
class RelatedResult:
    items: list[RelatedDocument]
    identical: list[IdenticalDocument]
    based_on_version: int | None   # document_chunks.version. 청크가 없으면 None
    reason: str | None             # 청크가 없으면 "not_indexed", 있으면 None


async def find_related(
    conn: psycopg.AsyncConnection,
    *,
    document_id: UUID,
    user_id: str | None = None,
    k: int = 10,
) -> RelatedResult: ...
```

`k` 검증은 `app.services.search.MAX_K`를 import해 재사용한다 (`1 <= k <= MAX_K`, 벗어나면 `ValueError`). 상수를 새로 정의하지 마라 — 이유: 과다 조회 `LIMIT`(`k * 10`)이 `hnsw.ef_search`(200)를 넘으면 **에러 없이 행이 모자란다**. 두 값의 관계를 한 곳에서만 관리해야 한다 (ADR-011 보강 4).

### 처리 순서 (이 순서를 지켜라)

**1) 청크 존재 여부와 기준 버전을 먼저 조회한다.**

```sql
SELECT count(*), min(version) FROM document_chunks WHERE document_id = %(id)s
```

**청크가 0건이면 벡터 쿼리를 실행하지 마라.** 이유: `avg(embedding)`이 NULL을 반환하고 `embedding <=> NULL`이 NULL이 되어 정렬이 무의미해진다. 예외도 경고도 없이 **무작위 문서 목록이 반환된다.**

분기 기준은 `documents.embedding_status`가 **아니라 청크 존재 여부**다. 재임베딩 중(`processing`)에도 이전 버전 청크가 남아 있으므로 정상 응답해야 한다.

**2) 동일 텍스트(identical)는 청크 유무와 무관하게 항상 계산한다.**

`content_hash`는 업로드 즉시 존재하므로 색인 전에도 유의미하다. `reason == "not_indexed"`인 응답에서도 `identical`은 채워져야 한다.

```sql
SELECT o.id, o.title
FROM documents me
JOIN documents o
  ON o.content_hash = me.content_hash AND o.id <> me.id
WHERE me.id = %(id)s
  AND (o.visibility = 'public' OR o.owner_id = %(user)s)
ORDER BY o.created_at, o.id
```

**3) 청크가 있으면 관련 문서 쿼리를 실행한다.** 아래 쿼리를 그대로 쓴다.

```sql
WITH me AS (
  SELECT avg(embedding) AS v FROM document_chunks WHERE document_id = %(id)s
),
cand AS (                      -- 1) 권한 필터를 건 상태로 벡터 인덱스 후보 확보
  SELECT c.document_id, c.embedding <=> (SELECT v FROM me) AS dist
  FROM document_chunks c
  JOIN documents d ON d.id = c.document_id
  WHERE c.document_id <> %(id)s
    AND (d.visibility = 'public' OR d.owner_id = %(user)s)
  ORDER BY c.embedding <=> (SELECT v FROM me)
  LIMIT %(k)s * 10
),
best AS (                      -- 2) 문서당 최소 거리 1건
  SELECT DISTINCT ON (document_id) document_id, dist
  FROM cand
  ORDER BY document_id, dist
)
SELECT d.id, d.title, d.tags, 1 - b.dist AS score   -- 3) 거리순 재정렬 + LIMIT
FROM best b JOIN documents d ON d.id = b.document_id
ORDER BY b.dist LIMIT %(k)s
```

**4) 트랜잭션과 세션 설정.**

```python
async with conn.transaction():
    await conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
    await conn.execute("SET LOCAL random_page_cost = 1.1")
    ...
```

`search.py`와 동일하게 `EF_SEARCH`를 import해 쓴다. `SET LOCAL`은 트랜잭션 밖에서 무효다. `random_page_cost = 1.1`이 없으면 플래너가 HNSW를 **아예 고르지 않는다** (VM 실측 624~785ms → 33~36ms, ADR-011 보강 5).

### 책임 경계 — 대상 문서의 접근 권한은 여기서 보지 않는다

이 함수는 **후보 문서에 대한 권한 필터만** 책임진다. "요청자가 이 문서를 볼 수 있는가"는 step 2의 라우터가 `services.documents.get_document()`로 먼저 확인한다. 여기서 중복 확인하지 마라 — 같은 판정이 두 곳에 생기면 어긋난다.

## 테스트

`backend/tests/test_related.py`를 **먼저** 작성한다. 최소한 아래를 덮어야 한다.

- 유사한 내용의 문서가 `score` 내림차순으로 반환된다
- 대상 문서 자신은 결과에 없다
- **타인의 private 문서가 결과에 없다** / 본인의 private 문서는 있다
- 청크가 0건이면 `items == []`, `reason == "not_indexed"`, `based_on_version is None`이고 **다른 문서 목록이 딸려 오지 않는다**
- 재임베딩 중(이전 버전 청크가 남아 있는 상태)에도 결과가 나오고 `based_on_version`이 `document_chunks.version`과 같다
- 같은 `content_hash`의 문서가 `identical`에 온다. 타인의 private 문서는 `identical`에도 오지 않는다
- 청크가 0건이어도 `identical`은 채워진다
- `k`가 0이거나 `MAX_K` 초과면 `ValueError`

DB는 실제 `pgvector/pgvector:pg17` 컨테이너를 쓴다. Mock·SQLite·인메모리 가짜로 대체하지 마라 — `vector` 연산자와 인덱스 동작은 원리상 Mock으로 검증할 수 없다.

## Acceptance Criteria

```bash
cd backend
source .venv/bin/activate
pytest tests/test_related.py -q      # 전부 통과
pytest -q                            # 기존 테스트도 통과
ruff check .                         # 린트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. (사전에 프로젝트 루트에서 `docker compose up -d`로 DB가 healthy인지 확인)
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가? (`backend/app/services/`)
   - ADR 기술 스택을 벗어나지 않았는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m4-mcp-gateway/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (다음 step이 쓸 함수/데이터클래스 이름을 반드시 포함)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `BEGIN READ ONLY`를 쓰지 마라. 이유: OpenProxy가 Replica로 라우팅해 방금 임베딩된 청크가 누락된다 (ADR-010)
- `DISTINCT ON` 직후에 `LIMIT`을 붙이지 마라. 이유: 유사도가 아니라 `document_id`(UUID) 순으로 잘린다 (ADR-011 보강 1)
- 권한 필터를 `cand` 밖으로 빼지 마라. 이유: 비공개 문서의 청크가 후보 자리를 차지하고 폐기되어 후보만 손해다. HNSW는 필터를 안에 둬도 정상 사용된다 (ADR-018 재개정 실측)
- `documents`에 문서 대표 벡터 컬럼을 추가하거나 평균을 저장하지 마라. 이유: 새로운 동기화 대상이 생겨 이 프로젝트가 해결하겠다는 문제를 하나 더 만든다 (ADR-018)
- 임시 테이블을 쓰지 마라. 중간 결과는 CTE로 처리한다. 이유: OpenProxy가 풀 백엔드 반납 시 `DISCARD ALL`을 하지 않아 다음 클라이언트로 누수된다 (ADR-022)
- `embedding_status`로 분기하지 마라. 이유: 재임베딩 중에도 이전 청크로 정상 응답해야 한다
- 마이그레이션 파일을 건드리지 마라. 이 step은 스키마를 바꾸지 않는다
- 기존 테스트를 깨뜨리지 마라
