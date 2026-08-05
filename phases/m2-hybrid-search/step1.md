# Step 1: 하이브리드 검색 서비스

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"검색 데이터 흐름" 절 전체**. SQL은 이 절의 블록을 그대로 옮긴다. 이어지는 **"네 가지 설계 결정이 이 쿼리에 반영되어 있다"** 4항목을 반드시 읽어라
- `/docs/ADR.md` — **ADR-010**(명시적 트랜잭션으로 Primary 강제) · **ADR-011**(`ef_search`와 `DISTINCT ON` 순서, **보강 4**: `ef_search` 상·하한, **보강 5**: `random_page_cost`) · **ADR-022**(임시 테이블 금지)
- `/docs/OPENSQL_RESEARCH.md` — **§12의 16·17번**(HNSW 재측정: JOIN은 인덱스를 막지 않는다, 진짜 변수는 `random_page_cost`였다)
- `/CLAUDE.md` — "아키텍처 규칙"의 CRITICAL 항목 전부. 특히 단일 SQL 결합 · plain `BEGIN` · `random_page_cost` · `ef_search` 하한 · `DISTINCT ON` 순서
- **이전 phase 산출물**:
  - `/backend/app/worker.py` — `process_once(conn, provider)`로 청크를 만든다. 테스트가 이 함수를 쓴다. `_vector_literal`이 벡터를 SQL에 넣는 방식도 참고하라(복사해 쓰되 **import 하지는 마라**, 아래 금지사항 참조)
  - `/backend/app/embeddings/__init__.py`, `/backend/app/embeddings/base.py` — `get_provider()`와 `EmbeddingProvider` Protocol
  - `/backend/tests/conftest.py` — `migrated_db` 픽스처(마이그레이션이 적용된 테스트 DB DSN)
  - `/backend/migrations/002_tables.sql`, `/backend/migrations/004_indexes.sql` — 테이블 컬럼과 HNSW 인덱스

이전 phase에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

이 step이 **이 과제의 심사 핵심 중 하나**를 만든다: 태그·유형·권한 같은 정형 필터와 pgvector 코사인 유사도를 **하나의 SQL로** 결합하는 검색.

DB에서 넓게 가져와 파이썬에서 걸러내면 이 가산점은 사라진다. 필터는 전부 SQL 안에 있어야 한다.

이 모듈은 REST API(step 4)와 MCP 서버(M4)가 **공유**한다. 검색 SQL은 여기 한 곳에만 존재한다.

## 작업

### 1. `backend/app/services/search.py`

```python
EF_SEARCH = 200                 # ADR-011 보강 4
CANDIDATE_MULTIPLIER = 5        # 후보를 k의 몇 배로 뽑는가
MAX_K = EF_SEARCH // CANDIDATE_MULTIPLIER   # = 40. 아래 설명을 읽어라

SEARCH_SQL: str                 # 모듈 상수. step 4가 응답에 실어 화면에 보여준다


@dataclass(frozen=True)
class SearchHit:
    document_id: UUID
    title: str
    tags: list[str]
    content_type: str
    chunk_index: int
    content: str
    score: float


async def search_documents(
    conn: psycopg.AsyncConnection,
    provider: EmbeddingProvider,
    *,
    query: str,
    user_id: str | None = None,
    tags: list[str] | None = None,
    content_type: str | None = None,
    k: int = 10,
) -> list[SearchHit]:
    """질의 텍스트를 임베딩해 정형 필터와 함께 단일 SQL로 검색한다."""
```

#### `provider`를 인자로 받는 이유 — 함수 안에서 `get_provider()`를 부르지 마라

`LocalProvider`는 모델을 **인스턴스 속성** `_model`에 lazy-load한다. 요청마다 `get_provider()`로 새 인스턴스를 만들면 **매 검색마다 2GB 모델을 다시 로드한다.** 에러는 나지 않고 그냥 느려지기만 하므로 발견하기 어렵다.

호출부가 프로세스 수명 동안 하나를 만들어 주입한다(step 4가 FastAPI lifespan에서 만든다). 이 함수는 받은 것을 쓰기만 한다.

#### `MAX_K`가 40인 이유 — 임의의 숫자가 아니다

`ef_search`(200)는 후보 `LIMIT`(`k * 5`)보다 **커야 한다.** 작으면 에러 없이 행이 모자란다 — 기본값 40에서 `LIMIT 50`을 걸면 40행만 나온다(CLAUDE.md 실측 기록). 그래서 `MAX_K = EF_SEARCH // CANDIDATE_MULTIPLIER`를 **계산식으로** 쓴다. 세 상수 중 하나를 바꾸면 나머지가 따라오고, 아래 테스트가 그 관계를 고정한다.

`k`가 `MAX_K`를 넘거나 1 미만이면 `ValueError`를 던진다. API(step 4)가 Pydantic으로도 막지만, MCP 서버(M4)가 이 함수를 직접 부르므로 서비스에도 방어가 필요하다.

#### SQL — `ARCHITECTURE.md` "검색 데이터 흐름"의 블록을 그대로 옮긴다

**절대 바꾸면 안 되는 것들** (각각이 실측이나 ADR에 근거한다):

1. **`WHERE d.embedding_status = 'ready'`를 넣지 마라.** 이유: 문서를 수정하면 트리거가 상태를 `pending`으로 되돌린다. 이 필터가 있으면 재임베딩이 끝날 때까지 문서가 검색에서 통째로 사라져, PRD의 "재임베딩 완료 전까지 이전 벡터로 검색이 계속된다"와 정면으로 모순된다.
2. **권한 술어 `(d.visibility = 'public' OR d.owner_id = %(user)s)`는 필수다.** 빼면 private 문서가 남의 검색에 나온다.
3. **`DISTINCT ON` 직후에 `LIMIT`을 붙이지 마라.** 순서는 **벡터 정렬 + `LIMIT k*5` → `DISTINCT ON (document_id)` → 거리순 재정렬 + `LIMIT k`**다. `DISTINCT ON ... ORDER BY document_id, dist LIMIT k`로 합치면 유사도가 아니라 `document_id`(UUID) 순으로 잘린다 (ADR-011).
4. **태그·유형·권한 필터를 `candidates` CTE 안에 둔다.** 밖으로 빼지 마라. 1차 실측이 "서브쿼리 내 JOIN이 HNSW를 막는다"로 결론냈으나 **재측정에서 재현되지 않았다**(ADR-018 재개정, `OPENSQL_RESEARCH.md` §12 17번). 밖으로 빼면 비공개 문서가 후보 자리를 차지해 손해만 본다.
5. **임시 테이블을 쓰지 마라.** 중간 결과는 CTE로 처리한다 (ADR-022). OpenProxy가 백엔드 반납 시 `DISCARD ALL`을 하지 않아 임시 테이블이 다음 클라이언트로 누수된다(실측).
6. **선택적 필터는 `(%(tags)s::text[] IS NULL OR d.tags && %(tags)s)` 형태로 SQL 안에서 분기한다.** 파이썬에서 WHERE 절을 문자열로 조립하지 마라 — `SEARCH_SQL`이 상수여야 step 4가 화면에 보여줄 수 있고, 조립은 인젝션 표면을 만든다.

#### 트랜잭션 — 여기서 조용히 깨진다

```python
async with conn.transaction():
    await conn.execute("SET LOCAL hnsw.ef_search = %s" % EF_SEARCH)   # 예시
    await conn.execute("SET LOCAL random_page_cost = 1.1")
    result = await conn.execute(SEARCH_SQL, params)
```

- **`SET LOCAL` 2개와 검색 쿼리는 같은 커넥션의 같은 `conn.transaction()` 블록 안에서 실행한다.** 블록 밖에서 `SET LOCAL`을 걸면(특히 커넥션이 autocommit이면) 즉시 사라져 `ef_search=40`·`random_page_cost=4`로 되돌아간다. **에러는 나지 않고 recall과 속도만 떨어진다.**
- **`BEGIN READ ONLY`·`START TRANSACTION READ ONLY`를 쓰지 마라** (ADR-010). OpenProxy 1.1.3은 이를 **의도적으로 Replica로 라우팅**하며, 복제 지연 보장이 없어 방금 임베딩된 청크가 누락된다. "읽기 전용이니 READ ONLY가 맞다"는 직관을 따르면 정확히 반대 결과가 나온다.
- `SET LOCAL`은 파라미터 바인딩이 되지 않는다. 값은 모듈 상수에서 온 정수·실수이며 사용자 입력이 아니다 — 그 사실을 주석으로 남겨라.

> M1에서 확인된 것: `hnsw.ef_search`는 pgvector 모듈이 세션에 로드된 뒤 등록되므로 갓 맺은 연결에서 `SHOW`는 실패한다. 하지만 `BEGIN` 직후 `SET LOCAL`을 걸고 벡터 연산이 뒤에 오는 **이 순서는 정상 동작한다**(`tests/test_indexes.py`가 고정). 설계를 바꿀 필요 없다.

#### 벡터를 SQL에 넣는 방법

`worker.py`와 같은 방식을 쓴다: 파이썬 리스트를 `'[0.1,0.2,...]'` 문자열 리터럴로 만들고 `%(qvec)s::vector`로 캐스팅한다. `pgvector` 파이썬 패키지나 numpy를 **추가하지 마라** — M1이 의도적으로 피했다.

### 2. `backend/tests/conftest.py`에 헬퍼 추가

step 2~5도 문서를 만들어야 하므로, 문서 생성과 임베딩 처리를 **conftest에 재사용 가능한 형태로** 둔다. 시그니처는 재량이나 아래 두 가지를 제공하라:

- 문서를 INSERT하고 `id`를 반환하는 헬퍼 (title·content·owner_id·visibility·content_type·tags를 지정 가능)
- **대기 중인 임베딩 잡을 전부 처리하는 헬퍼** — `app.worker.process_once`를 더 처리할 잡이 없을 때까지 반복 호출한다

> **청크를 `document_chunks`에 직접 INSERT하지 마라.** 워커를 태워서 만들어라. 이유: 트리거 → 잡 → 워커 → 청크로 이어지는 파이프라인 전체가 검색 테스트에서 함께 검증된다. 직접 INSERT하면 그 연결이 끊기고, `document_chunks.version`을 손으로 채우다 실제 워커와 다른 값을 넣기 쉽다.
>
> **워커 함수에 넘기는 커넥션은 `autocommit=True`여야 한다** (M1 step 6에서 확인된 함정). 아니면 `claim_job`의 '즉시 커밋'이 조용히 SAVEPOINT로 바뀐다. 반면 **검색 함수에 넘기는 커넥션은 그러면 안 된다** — 위 트랜잭션 절을 보라. 테스트에서 두 커넥션의 성격이 다르다는 점에 주의하라.

### 3. `backend/tests/test_search.py` — 먼저 작성한다

**구현보다 테스트를 먼저 작성하고 실패를 확인한 뒤 구현하라.**

#### 픽스처 작성 규칙 — `FakeProvider`의 성질에서 나온 제약

`FakeProvider`는 `text.split()`으로 나눈 **어절**마다 차원 하나를 배정하는 feature hashing이다. 한국어 조사가 붙으면 다른 토큰이 된다 — `워커의`와 `워커는`은 겹치지 않는다. 이 성질은 **의도적으로 유지하기로 결정됐다**(char n-gram을 추가하면 무관 문서 간 유사도가 0.000에서 평균 0.107로 올라 "권한 없는 문서가 안 나온다" 류의 단언이 흔들리고, 검증 대상이 BGE-M3가 아니라 n-gram 매칭이 되어버린다 — 실측 비교 후 결정).

따라서:

1. **질의에 쓸 어절이 문서 본문에 그대로 등장하도록 픽스처를 짜라.** 실측상 어절이 하나만 겹쳐도 코사인 +0.15~+0.30으로 확실히 분리된다.
2. **무관 문서끼리는 유사도가 정확히 0으로 동점이라 정렬이 비결정적이다.** "무관 문서가 3위"처럼 **순위를 단언하지 마라.** `관련 문서가 1위` 또는 `관련 문서의 score > 무관 문서의 score`만 단언하라.
3. **순위 테스트의 docstring에 검증 대상을 명시하라**: "여기서 확인하는 것은 **SQL이 거리순으로 정렬하는가**이지 검색 품질이 아니다. 품질은 BGE-M3의 성질이며 `FakeProvider`로는 원리상 검증할 수 없다."

#### 최소 아래를 확인한다

1. **관련 문서가 상위에 온다** — 질의 어절을 공유하는 문서가 1위.
2. **private 문서가 타인의 검색에 나오지 않는다** — 소유자가 아닌 `user_id`로 검색.
3. **소유자 본인은 자기 private 문서를 본다** — 2번의 짝. 필터가 과하게 걸리지 않았음을 확인한다.
4. **`user_id=None`(익명)이면 public만 나온다.** SQL에서 `owner_id = NULL`이 NULL로 평가되어 자연히 걸러진다 — 파이썬에서 분기하지 않고 이 성질을 쓴다.
5. **태그 필터** — `tags=["규정"]`이면 그 태그를 가진 문서만. 배열 겹침(`&&`) 동작을 확인하라.
6. **유형 필터** — `content_type="pdf"`이면 pdf만.
7. **필터와 벡터가 함께 걸린다** — 태그 필터 + 질의를 동시에 주었을 때, 태그는 맞지만 내용이 무관한 문서보다 둘 다 맞는 문서가 위에 온다.
8. **문서당 1건** — 질의 어절이 여러 청크에 반복되는 긴 문서를 만들고, 결과에 그 문서가 **한 번만** 나오는지 확인한다. 긴 문서가 상위 k를 도배하면 실패다.
9. **`DISTINCT ON` 순서 회귀** — 문서 여러 개가 각각 여러 청크를 가진 상태에서 상위 k가 **거리순**인지 확인한다. `document_id`(UUID) 순으로 잘리면 실패다. UUID는 랜덤이므로 이 테스트는 문서를 충분히(예: 5개 이상) 만들어야 의미가 있다.
10. **재임베딩 중에도 결과가 비지 않는다** — 청크가 있는 문서의 본문을 수정해 `embedding_status`를 `pending`으로 되돌린 뒤(워커를 돌리지 않은 상태) 검색하면, **이전 버전 청크로 그 문서가 여전히 나온다.**
11. **청크가 없는 문서는 나오지 않는다** — 업로드 직후(워커 미실행) 상태.
12. **`k` 검증** — `k=0`·`k=MAX_K+1`은 `ValueError`. `k=MAX_K`는 정상 동작한다.
13. **`ef_search` 회귀 테스트** — 문서를 충분히 만들고(예: `MAX_K`보다 넉넉히 많게) `k=MAX_K`로 검색해 **결과가 `MAX_K`건 나오는지** 확인한다. `ef_search`가 후보 `LIMIT`보다 작으면 여기서 행이 모자라 실패한다. **이것이 CLAUDE.md에 기록된 "에러 없이 행이 모자란다"에 대한 회귀 테스트다.**
14. **`SET LOCAL`이 트랜잭션 안에서 적용된다** — 같은 패턴(`conn.transaction()` 안에서 `SET LOCAL` → `SHOW`)으로 `hnsw.ef_search`가 200, `random_page_cost`가 1.1임을 확인하고, 트랜잭션 종료 후 원복되는지도 확인한다.
15. **단일 쿼리 확인** — `EXPLAIN`을 실행해 태그 필터와 벡터 정렬이 **하나의 실행 계획** 안에 있음을 확인한다. 이슈 #7의 완료 조건이다. 로컬 컨테이너는 데이터가 적어 플래너가 Seq Scan을 고를 수 있으므로, **인덱스 사용 여부를 단언하려면** `SET LOCAL enable_seqscan = off`로 강제한 상태에서 확인하라(M1 `tests/test_indexes.py`가 쓴 방법). 계획에 `documents` 필터가 나타나는지도 함께 본다.
16. **결과 정렬** — 반환된 `SearchHit`의 `score`가 내림차순이다.

## Acceptance Criteria

```bash
docker compose up -d              # 프로젝트 루트에서. pgvector 컨테이너가 healthy여야 한다
cd backend
.venv/bin/ruff check .
.venv/bin/pytest tests/test_search.py -v
.venv/bin/pytest                  # 전체 통과
cd ..
bash scripts/check.sh             # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `app/services/search.py`에 있는가? SQL이 이 파일 **한 곳에만** 존재하는가?
   - 필터가 전부 SQL 안에 있는가 — 파이썬에서 결과를 후처리 필터링하지 않았는가?
   - `WHERE embedding_status = 'ready'`가 없는가?
   - `DISTINCT ON` 다음에 거리순 재정렬 + `LIMIT`이 오는가?
   - `SET LOCAL` 2개가 검색 쿼리와 같은 트랜잭션 안에 있는가?
   - `BEGIN READ ONLY`를 쓰지 않았는가?
   - 임시 테이블을 만들지 않았는가?
   - 새 런타임 의존성(`pgvector`·`numpy`)을 추가하지 않았는가?
3. 결과에 따라 `phases/m2-hybrid-search/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **`search_documents`의 정확한 시그니처, `SearchHit` 필드, `SEARCH_SQL`·`MAX_K` 상수명, provider를 주입받는다는 점, conftest에 추가한 헬퍼 이름을 반드시 포함시켜라.** step 2·4가 이것들을 쓴다.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **함수 안에서 `get_provider()`를 부르지 마라.** 이유: `LocalProvider`는 모델을 인스턴스 속성에 담으므로, 요청마다 새로 만들면 매 검색이 2GB 모델을 다시 로드한다. 에러 없이 느려지기만 한다.
- **`from app.worker import _vector_literal` 하지 마라.** 이유: 검색이 워커에 의존하는 방향이 되고 private 이름에 결합된다. 4줄짜리 자체 헬퍼가 낫다.
- **`BEGIN READ ONLY`를 쓰지 마라.** 이유: OpenProxy가 Replica로 라우팅해 방금 임베딩된 청크가 누락된다 (ADR-010).
- **`SET LOCAL`을 트랜잭션 밖에 두지 마라.** 이유: 조용히 무효가 되어 `ef_search=40`·`random_page_cost=4`로 돌아간다. 에러가 나지 않아 발견되지 않는다.
- **WHERE 절을 파이썬 문자열로 조립하지 마라.** 이유: `SEARCH_SQL`이 상수여야 step 4가 실행 SQL을 화면에 보여줄 수 있고, 조립은 인젝션 표면을 만든다.
- **DB에서 넓게 가져와 파이썬에서 필터링하지 마라.** 이유: "정형 필터 + 벡터를 단일 SQL로"가 이 과제의 가산점 항목이다.
- **`document_chunks`에 테스트가 직접 INSERT하지 마라.** 이유: 워커를 태우면 트리거→잡→워커→청크 파이프라인이 함께 검증된다.
- **`FakeProvider`를 수정하지 마라.** 이유: 어절 기반 토큰화는 실측 비교 후 유지하기로 결정된 사항이다. char n-gram을 추가하면 무관 문서 간 유사도가 0.000에서 0.107로 올라 권한·필터 테스트의 대비가 약해진다.
- **`app/api/` 아래 파일을 만들지 마라.** 이유: step 2·4의 범위다.
- **관련 문서·태그 추천(`avg(embedding)` 쿼리)을 만들지 마라.** 이유: M4의 범위다.
- **키워드 FTS·RRF를 만들지 마라.** 이유: ADR-016은 조건부 채택이며 M6이다.
- 기존 테스트를 깨뜨리지 마라.
