# Step 3: 벡터 인덱스

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"벡터 인덱스" 절 전체**, "검색 데이터 흐름" 절(이 인덱스를 실제로 쓰는 쿼리 구조)
- `/docs/ADR.md` — **ADR-002**(HNSW 채택 근거, IVFFlat·pgvectorscale 기각 이유, **"인덱스 생성 실행은 M0에서 확인한다"는 미해소 항목**), **ADR-011**(`ef_search`로 post-filter recall 완화 / `iterative_scan`은 실측 전까지 켜지 않는다)
- `/docs/OPENSQL_RESEARCH.md` — §0 배포판 확정 사항(pgvector **0.8.1**), §8, §12 검증 목록
- **이전 step 산출물**: `/backend/migrations/002_tables.sql`(`document_chunks.embedding`), `/backend/tests/conftest.py`(`migrated_db` 픽스처)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

ADR-002는 HNSW를 채택하면서 **"인덱스 생성 실행은 M0에서 확인한다 — 버전이 맞아도 빌드 옵션 등으로 막힐 가능성은 남는다"**를 미해소 항목으로 남겼다. M0에서는 확장을 설치하지 않아 확인하지 못했다. 이 step이 그 항목을 해소한다.

로컬은 `pgvector/pgvector:pg17`, 실 배포판은 pgvector 0.8.1이다. **로컬에서 통과했다고 실 클러스터에서 통과한다는 보장은 아니다** — 실 검증은 VM 환경에서 별도로 수행한다. 이 step은 로컬 확인까지가 범위다.

## 작업

### 1. `backend/migrations/004_indexes.sql`

```sql
CREATE INDEX idx_chunks_embedding ON document_chunks
  USING hnsw (embedding vector_cosine_ops);
```

- **이 인덱스 하나만 만든다.** `m`·`ef_construction`은 기본값(16 / 64)을 쓰며 명시하지 않는다 — 튜닝 근거가 아직 없다.
- **코사인 거리(`vector_cosine_ops`)다.** L2(`vector_l2_ops`)나 내적으로 바꾸지 마라 — 검색 쿼리가 `<=>` 연산자를 쓰고, BGE-M3의 정규화 임베딩과 맞춘 선택이다 (ADR-002).
- 조회용 보조 인덱스(`documents(embedding_status)`, `embedding_jobs(status, next_attempt_at)` 등)를 **만들지 마라.** 데모 규모에서 필요가 입증되지 않았고, 요청받지 않은 것은 만들지 않는다.

### 2. `backend/tests/test_indexes.py` — 먼저 작성한다

`tdd-guard`는 `*_indexes.sql`을 테스트 면제로 두지만, `CLAUDE.md`는 **"인덱스가 검색 계획에 실제로 쓰이는지 확인이 필요하면 테스트를 직접 추가하라"**고 한다. ADR-002의 미해소 항목이 정확히 그 경우이므로 테스트를 만든다. **SQL보다 테스트를 먼저 작성하라.**

`migrated_db` 픽스처를 쓰고 최소 아래를 검증한다.

1. **인덱스가 실제로 만들어졌다** — `pg_class`/`pg_am`을 조회해 `idx_chunks_embedding`의 접근 방식이 `hnsw`인지 확인한다. `pg_indexes.indexdef` 문자열만 보고 넘기지 마라 — 정의가 남아 있어도 인덱스 방식이 다를 수 있다.
2. **플래너가 이 인덱스를 쓴다** — 청크를 여러 건 넣고, 트랜잭션 안에서 `SET LOCAL enable_seqscan = off`를 건 뒤 `EXPLAIN`으로 `ORDER BY embedding <=> ...  LIMIT k` 쿼리가 `idx_chunks_embedding`을 타는지 확인한다.
   - **`enable_seqscan = off`가 필요한 이유를 테스트 주석에 남겨라**: 행이 적으면 플래너가 순차 스캔을 고르는 것이 정상이며, 그것은 인덱스 결함이 아니다. 여기서 확인하려는 것은 **"인덱스를 쓸 수 있는가"**다.
3. **`SET LOCAL hnsw.ef_search = 200`이 동작한다** — 트랜잭션 안에서 설정하고 `SHOW`로 값이 반영됐는지, 트랜잭션 종료 후 원복되는지 확인한다. ADR-011이 검색에서 쓸 파라미터이므로 여기서 가용성을 확인해 둔다.
4. **거리 정렬이 의미대로 동작한다** — 서로 다른 벡터 몇 개를 넣고 `<=>` 정렬 결과가 예상 순서인지 확인한다. 인덱스가 아니라 **연산자와 차원 설정이 맞는지**를 보는 것이다.

> **`hnsw.iterative_scan`을 켜지 마라.** pgvector 0.8+에서 쓸 수 있지만, ADR-011 보강 3이 **"실측 없이 켜지 않는다"**고 명시했다. 필터 결합 검색의 recall을 측정한 뒤 부족하면 그때 켜고 결과를 ADR에 기록한다. 측정 대상인 검색 쿼리는 아직 존재하지 않는다.

### 3. 실측값 기록

테스트를 돌리는 김에 아래를 확인해 **step summary에 수치로 남겨라.** ADR-002의 미해소 항목을 해소했다는 근거가 된다.

```sql
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

- 로컬 컨테이너의 pgvector 버전
- 인덱스 생성이 성공했는지, 걸린 시간(체감 수준으로 충분)

## Acceptance Criteria

```bash
docker compose up -d
cd backend
.venv/bin/ruff check .
.venv/bin/pytest tests/test_indexes.py -v
.venv/bin/pytest                  # 전체 통과
cd ..
bash scripts/check.sh             # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 인덱스가 `hnsw (embedding vector_cosine_ops)`인가? (ADR-002)
   - `004_indexes.sql`에 HNSW 외의 인덱스가 들어가지 않았는가?
   - 파셜 유니크 인덱스가 `002_tables.sql`에 그대로 있는가? (여기로 옮기지 마라)
3. 결과에 따라 `phases/m1-db-layer/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **pgvector 버전과 인덱스 생성 성공 여부를 수치로 포함시켜라.** ADR-002의 미해소 항목에 대한 근거다.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - **HNSW 인덱스 생성 자체가 실패하면 `"status": "blocked"`** + 실패 SQL과 에러 메시지를 `blocked_reason`에 적고 즉시 중단하라. 이유: ADR-002의 전제가 무너진 것이므로 IVFFlat·pgvectorscale 전환은 설계 결정이며 step 세션이 임의로 바꿀 수 없다.

## 금지사항

- **`hnsw.iterative_scan`을 켜지 마라.** 이유: ADR-011 보강 3 — 실측 없이 켜지 않는다.
- **IVFFlat이나 pgvectorscale로 바꾸지 마라.** 이유: ADR-002의 결정이다. HNSW 생성이 실패하면 전환하지 말고 `blocked`로 보고하라.
- **`m`·`ef_construction`을 임의로 조정하지 마라.** 이유: 튜닝 근거가 없다. 기본값을 쓴다.
- **보조 인덱스를 추가하지 마라.** 이유: 요청받지 않았고 데모 규모에서 필요가 입증되지 않았다.
- **검색 쿼리(`services/search.py`)를 만들지 마라.** 이유: 검색은 후속 phase의 범위다. 이 step은 인덱스가 존재하고 사용 가능함까지만 확인한다.
- **`005_fts.sql`(tsvector·GIN)을 만들지 마라.** 이유: ADR-016은 조건부이며 core 완성 후다.
- 기존 테스트를 깨뜨리지 마라.
