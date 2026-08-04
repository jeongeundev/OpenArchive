# Step 1: 스키마

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"DB 스키마" 절 전체**(4개 테이블 DDL과 주석), **"`document_chunks.version`의 용도" 절**, "정합성 보장" 표
- `/docs/ADR.md` — **ADR-001**(트랜잭셔널 아웃박스·파셜 유니크 인덱스로 코얼레싱), **ADR-003**(`vector(1024)` 고정), **ADR-005**(번호 붙은 raw SQL)
- **이전 step 산출물**: `/backend/app/migrations.py`(러너 시그니처), `/backend/tests/conftest.py`(테스트 DB 픽스처)
- `/scripts/hooks/tdd-guard.sh` — `002_tables.sql`은 `backend/tests/test_tables.py`를 요구한다. 테스트가 없으면 파일 쓰기가 차단된다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

이 step이 만드는 4개 테이블이 이 과제의 심사 핵심을 담는다. **테이블 정의 자체가 산출물**이므로 읽기 좋은 SQL로 쓰고, `ARCHITECTURE.md`에 있는 설계 의도 주석을 SQL 파일에도 남긴다.

트리거는 다음 step에서 만든다. 이 step의 테이블에는 아직 아무 트리거도 걸리지 않으므로, 문서를 INSERT해도 잡이 생기지 않는 것이 정상이다.

## 작업

### 1. `backend/migrations/001_extensions.sql`

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

- 이것 하나만 둔다. `pgcrypto`를 넣지 마라 — `gen_random_uuid()`는 PostgreSQL 13부터 코어에 내장되어 있다.
- `pgvectorscale`을 설치하지 마라 — 번들되어 있지만 채택하지 않았다 (ADR-002).

### 2. `backend/migrations/002_tables.sql`

`ARCHITECTURE.md`의 "DB 스키마" 절에 있는 DDL을 **그대로** 옮긴다. 컬럼·타입·기본값·제약을 임의로 추가하거나 빼지 마라.

- `documents` — `CONSTRAINT documents_content_not_blank CHECK (length(btrim(content)) > 0)` 포함
- `document_versions` — `PRIMARY KEY (document_id, version)`, `ON DELETE CASCADE`
- `document_chunks` — `embedding vector(1024) NOT NULL`, `UNIQUE (document_id, chunk_index)`, `ON DELETE CASCADE`
- `embedding_jobs` — `ON DELETE CASCADE`

그리고 **파셜 유니크 인덱스를 이 파일에 함께 둔다**:

```sql
-- 핵심: 문서당 pending 잡은 1개만 — DB 계층 코얼레싱
CREATE UNIQUE INDEX uq_pending_job_per_doc
  ON embedding_jobs(document_id) WHERE status = 'pending';
```

**왜 `004_indexes.sql`이 아니라 여기인가** — 이것은 성능 인덱스가 아니라 **데이터 무결성 제약**이다. 다음 step의 트리거가 `ON CONFLICT DO NOTHING`으로 코얼레싱하는데, 이 인덱스가 없으면 충돌 대상이 없어 잡이 무한정 쌓인다. 즉 트리거의 전제조건이다. 또한 `tdd-guard`는 `*_tables.sql`에는 테스트를 요구하지만 `*_indexes.sql`은 면제하므로, 심사 핵심인 코얼레싱 제약을 테스트로 보호되는 파일에 두는 것이 맞다.

기타 규칙:

- `CREATE TABLE IF NOT EXISTS`를 쓰지 마라. 러너가 적용 이력으로 멱등을 보장하므로 불필요하고, `IF NOT EXISTS`는 "이미 다른 정의로 존재하는" 상태를 조용히 넘긴다.
- `updated_at`을 자동 갱신하는 트리거를 만들지 마라. 요청받지 않았고, `updated_at`은 API가 UPDATE 시 함께 쓴다.
- 상태 컬럼(`embedding_status`, `embedding_jobs.status`)에 `CHECK` 제약이나 enum 타입을 추가하지 마라. 이유: `ARCHITECTURE.md`가 주석으로만 값 집합을 명시했다. 설계에 없는 제약을 추가하지 않는다.

### 3. `backend/tests/conftest.py` — `migrated_db` 픽스처 추가

```python
@pytest.fixture
async def migrated_db(clean_db: str) -> str:
    """실제 backend/migrations/ 를 적용한 테스트 DB의 DSN."""
```

- `app.migrations.run_migrations`를 호출해 적용한다. 테스트가 스키마를 직접 SQL로 만들지 않게 하는 것이 목적이다 — **테스트가 실제 마이그레이션 파일을 검증 대상으로 삼아야 한다.**
- 이후 모든 DB 테스트(트리거·인덱스·워커)가 이 픽스처를 쓴다.

### 4. `backend/tests/test_tables.py` — 먼저 작성한다

**구현(SQL)보다 테스트를 먼저 작성하라.** tdd-guard가 테스트 없는 `002_tables.sql` 쓰기를 차단하므로 순서가 강제된다.

최소 아래를 검증한다. 전부 `migrated_db`에 실제로 SQL을 실행해 확인한다.

1. **테이블·확장 존재** — `vector` 확장이 설치되어 있고 4개 테이블이 모두 있다.
2. **빈 본문 차단** — `content`가 공백/탭/개행만인 문서를 INSERT하면 CHECK 제약 위반으로 실패한다. 이유: 텍스트를 추출하지 못한 문서가 검색에 영원히 잡히지 않는 유령 행이 되는 것을 DB에서 막는 장치다.
3. **기본값** — INSERT 시 `version=1`, `embedding_status='pending'`, `visibility='public'`, `tags='{}'`가 채워진다.
4. **벡터 차원 고정** — `document_chunks.embedding`에 1024차원이 아닌 벡터를 넣으면 실패한다.
5. **청크 유니크** — 같은 `(document_id, chunk_index)`를 두 번 넣으면 실패한다.
6. **버전 이력 PK** — 같은 `(document_id, version)`을 두 번 넣으면 실패한다.
7. **CASCADE 삭제** — 문서를 삭제하면 그 문서의 `document_versions`·`document_chunks`·`embedding_jobs`가 함께 사라진다. **이 과제가 주장하는 "삭제 정합성"의 근거이므로 세 테이블 모두 확인하라.**
8. **코얼레싱 제약** — 같은 문서에 `status='pending'` 잡을 두 건 넣으면 두 번째가 유니크 위반으로 실패한다. 그리고 **첫 잡을 `done`으로 바꾸면 새 `pending` 잡을 넣을 수 있다** — 파셜 인덱스가 `pending`만 대상으로 한다는 것이 이 두 번째 확인으로 증명된다.

> **이 테스트에서는 `embedding_jobs`에 직접 INSERT해도 된다.** `CLAUDE.md`의 금지 규칙은 **애플리케이션 코드**를 대상으로 한다. 여기서는 제약 그 자체가 검증 대상이고, 트리거는 아직 존재하지 않으므로 직접 INSERT가 유일한 방법이다. 단, `app/` 아래 코드에서는 절대 하지 마라.

## Acceptance Criteria

```bash
docker compose up -d
cd backend
.venv/bin/ruff check .
.venv/bin/pytest tests/test_tables.py -v
.venv/bin/pytest                  # 전체 통과
cd ..
bash scripts/check.sh             # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - DDL이 `ARCHITECTURE.md`의 "DB 스키마" 절과 컬럼·타입·제약 단위로 일치하는가?
   - `vector(1024)`인가? (ADR-003 — 프로바이더가 바뀌어도 차원은 고정)
   - 파셜 유니크 인덱스가 `002_tables.sql`에 있는가?
   - 테스트가 실제 컨테이너에서 제약 위반을 확인하는가, 아니면 존재만 확인하는가?
3. 결과에 따라 `phases/m1-db-layer/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **테이블·제약 이름과 `migrated_db` 픽스처 추가 사실을 포함시켜라.** 다음 step의 트리거 테스트가 이것들을 쓴다.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **트리거·트리거 함수를 만들지 마라** (`003_triggers.sql` 포함). 이유: 다음 step의 범위다. 지금 만들면 이 step의 테스트가 "제약"과 "트리거 동작"을 섞어 검증하게 된다.
- **HNSW 인덱스를 만들지 마라.** 이유: `004_indexes.sql`은 step 3의 범위다.
- **`005_fts.sql`(tsvector·GIN)을 만들지 마라.** 이유: ADR-016은 **조건부 채택**이며 core 완성 후에 착수한다.
- **상태 컬럼에 `CHECK`나 enum 타입을 추가하지 마라.** 이유: 설계에 없다. 요청받지 않은 제약을 넣지 않는다.
- **애플리케이션 코드(`app/`)에서 `embedding_jobs`에 INSERT하는 코드를 만들지 마라.** 이유: `CLAUDE.md` CRITICAL. 잡 생성은 다음 step의 트리거가 담당한다.
- **`document_chunks`에 `documents`의 필터 컬럼(tags·visibility 등)을 비정규화하지 마라.** 이유: ADR-011의 에스컬레이션 경로이며, **필요가 실측으로 입증되기 전에는 하지 않는다.**
- 기존 테스트를 깨뜨리지 마라.
