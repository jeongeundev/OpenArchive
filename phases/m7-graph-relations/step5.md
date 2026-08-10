# Step 5: edges-table

## 배경 — 스키마가 결정을 그대로 담아야 한다

ADR-029가 정한 것을 테이블 하나로 옮긴다. **노드는 문서**, 청크는 위치가 의미 있는 관계에만
채우는 부가 정보다.

```
document_edges
  src_document_id  ─┐
  dst_document_id  ─┴─ 노드는 항상 문서 (FK · CASCADE)
  kind               overlaps | points_to | broader | related
  src_chunk_index  ─┐
  dst_chunk_index  ─┴─ points_to 계열만 채운다. 나머지는 NULL
  score              판정 근거값
```

**`dst_document_id`는 `NOT NULL`이다.** 위키링크는 대상이 제목 문자열이라 이 테이블에
들어오지 않으며(ADR-029 결정 2), 저장 위치는 m9가 정한다.

## 읽어야 할 파일

- `docs/ADR.md` **ADR-029** — 이 테이블의 근거 전부
- `backend/migrations/002_tables.sql` — 기존 테이블의 주석 밀도·제약 표현 방식·
  `IF NOT EXISTS`를 쓰지 않는 이유(ADR-005). **같은 스타일로 쓴다**
- `backend/migrations/004_indexes.sql` — 인덱스 파일의 형식
- `backend/tests/test_tables.py` · `test_indexes.py` — 기존 스키마 테스트가 무엇을 어떻게
  단언하는지. 새 테스트를 같은 방식으로 쓴다
- `docs/OPENSQL_RESEARCH.md` **§14** — `NEIGHBOR_N` 등 상수. 주석에 근거로 인용한다

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_tables.py`에 추가한다 — **tdd-guard가 `*_tables.sql`에 대해 이 파일을
요구한다.** 인덱스는 `test_indexes.py`에.

단언할 것:

- `document_edges`가 존재하고 컬럼·타입이 맞는가
- **자기 자신을 가리키는 edge가 거부되는가** (`src = dst` INSERT가 실패)
- **`kind`에 정의되지 않은 값이 거부되는가**
- **같은 (src, dst, kind, 청크쌍)이 두 번 들어가지 않는가** — `NULL` 청크쌍에서도
  중복이 막히는지 반드시 확인한다(NULL은 기본적으로 서로 다르게 취급되므로,
  표현식 유니크 인덱스가 없으면 **조용히 중복이 쌓인다**)
- **문서를 지우면 그 문서가 걸린 edge가 양쪽 방향 모두 사라지는가** (CASCADE)
- 순회 인덱스가 실제로 쓰이는가 (`EXPLAIN`에 인덱스 이름이 나오는가)

### 2) `backend/migrations/005_trgm_extensions.sql`

```sql
CREATE EXTENSION pg_trgm;
```

- **`broader` 판정이 `word_similarity`에 의존하므로 m7에 필요하다.** m6 step 4가 이 확장을
  "m9"로 적어 둔 것은 RRF 맥락이었고, 방향 판정이 먼저 쓴다
- `pg_trgm` 1.6은 **로컬 컨테이너와 실 VM 양쪽에 이미 있다**(contrib) — 커스텀 이미지도
  `.deb`도 필요 없다(#29 실측, ADR-026). 그래서 `DO $$ IF EXISTS $$` 가드로 감싸지 않는다
- 파일 상단 주석에 **왜 여기서 만드는지**와 **m9의 RRF가 같은 확장을 재사용한다**는 것을 적는다

### 3) `backend/migrations/006_edges_tables.sql`

```sql
CREATE TABLE document_edges (
  src_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  dst_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kind            text NOT NULL,
  src_chunk_index int,
  dst_chunk_index int,
  score           real NOT NULL,
  ...
);
```

제약을 반드시 넣는다.

| 제약 | 막는 것 |
|---|---|
| `CHECK (src_document_id <> dst_document_id)` | 자기 자신과의 관계. 순회가 무한히 맴돈다 |
| `CHECK (kind IN ('overlaps','points_to','broader','related'))` | 오타로 생긴 새 종류가 조용히 쌓이는 것 |
| **표현식 유니크 인덱스** | 같은 관계의 중복. `COALESCE(src_chunk_index, -1)`로 NULL을 값으로 접는다 |

**주석에 담을 것** — 기존 마이그레이션이 그렇듯 *"왜"*가 파일 안에 있어야 한다.

- **청크 id가 아니라 `chunk_index`인 이유**: `document_chunks.id`는 `bigserial`이고
  워커가 재임베딩할 때 전량 교체하므로(`worker.py:132`) id가 전부 바뀐다
- **`dst_document_id`가 `NOT NULL`인 이유**: 위키링크는 이 테이블에 들어오지 않는다
- **`broader`의 방향**: `src`가 `dst`보다 **포괄적**이다. 반대는 행을 뒤집어 표현한다
- **`related`의 뜻**: 방향 판정에 실패한 `broader` 폴백이다 (#35)
- **`score`가 종류마다 다른 의미**라는 것: `overlaps`는 매칭 비율, `points_to`는 유사도,
  `broader`는 `word_similarity` 차. **한 컬럼에 다른 척도가 들어오므로 종류를 섞어
  정렬하면 안 된다** — 이 경고를 주석에 남긴다

### 4) `backend/migrations/007_edges_indexes.sql`

- **순방향 순회**: `(src_document_id, kind)`
- **역방향 순회·백링크**: `(dst_document_id, kind)`
- 트리거가 `WHERE src = ? OR dst = ?`로 지우므로 **둘 다 삭제 경로에도 쓰인다**

> 인덱스가 실제로 계획에 나오는지 `test_indexes.py`가 확인한다 — `CLAUDE.md`가 남긴
> *"인덱스가 검색 계획에 실제로 쓰이는지 확인이 필요하면 테스트를 직접 추가하라"*가 이 자리다.
> **행이 적으면 플래너가 Seq Scan을 고르므로**, 테스트는 충분한 행을 넣거나
> `enable_seqscan = off`로 계획 형태만 확인한다.

## Acceptance Criteria

```bash
cd backend

# 1) 테스트가 있고 통과하는가
python -m pytest tests/test_tables.py tests/test_indexes.py -q

# 2) 마이그레이션 세 파일이 생겼는가
ls migrations/005_trgm_extensions.sql migrations/006_edges_tables.sql migrations/007_edges_indexes.sql

# 3) 실제로 적용되는가 — 깨끗한 DB에 전량 적용
#    compose 파일은 저장소 루트에 있다. 마이그레이션 CLI는 없고 앱 lifespan이 run_migrations를 부르므로
#    직접 호출한다 — `A || B & sleep; kill %1`은 `&`가 `||` 리스트 전체를 백그라운드로 보내 어긋난다
docker compose -f ../docker-compose.yml down -v && docker compose -f ../docker-compose.yml up -d && sleep 5
python -c "import asyncio; from app.config import get_settings; from app.migrations import run_migrations; print(asyncio.run(run_migrations(get_settings().database_url)))"
#   → 적용된 파일 목록에 005·006·007이 있어야 한다

# 4) 스키마가 결정대로인가
psql "$DATABASE_URL" -c "\d document_edges"
#   → dst_document_id 가 not null 인지 눈으로 확인

# 5) 중복이 실제로 막히는가 — NULL 청크쌍에서도
psql "$DATABASE_URL" -c "SELECT indexdef FROM pg_indexes WHERE tablename='document_edges'" | grep -i coalesce

# 6) pg_trgm이 살아 있고 비대칭 함수가 도는가
psql "$DATABASE_URL" -c "SELECT word_similarity('벡터 검색', '이 문서는 벡터 검색을 다룬다')"
#   → 0보다 큰 값

# 7) 기존 테스트 전부 통과하는가
python -m pytest -q

# 8) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - **테스트가 구현보다 먼저 쓰였는가?** (하네스에서 tdd-guard는 무력하다)
   - **NULL 청크쌍의 중복이 실제로 막히는가?** 단순 `UNIQUE(...)`는 NULL을 서로 다르게
     보므로 **중복이 조용히 쌓인다.** 표현식 인덱스인지 확인한다
   - **CASCADE가 양방향 모두 동작하는가?** `dst` 쪽 FK를 빼먹으면 문서를 지운 뒤
     **존재하지 않는 문서를 가리키는 edge**가 남아 순회가 유령 노드를 만난다
   - **`IF NOT EXISTS`를 쓰지 않았는가?** 멱등은 러너의 적용 이력이 담당한다 (ADR-005)
   - **`score`의 척도 경고가 주석에 있는가?** 없으면 step 8·9에서 종류를 섞어 정렬하는
     실수가 나온다
3. 결과에 따라 `phases/m7-graph-relations/index.json`의 step 5를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **기존 마이그레이션(001~004)을 수정하지 마라.** 이유: 러너의 적용 이력이 이미 있어
  재적용되지 않는다. 변경은 새 번호 파일로만 (ADR-005)
- **`dst_document_id`를 nullable로 만들지 마라.** 이유: 위키링크를 미리 담으려는 것인데,
  m9가 잘리면 영원히 NULL인 컬럼과 안 타는 CHECK 분기가 남는다 (#38 결정 5)
- **`kind`에 `follows`·`precedes`·`shares_tag`·`revises`를 넣지 마라.** 이유: 저장하지 않는
  관계다 (ADR-029 결정 2). CHECK 목록에 넣어 두면 step 6이 채우게 된다
- **트리거를 만들지 마라.** 이유: step 6이다. 이 step은 그릇만 만든다
- **`IF NOT EXISTS`·`DO $$ ... $$` 가드를 쓰지 마라.** 이유: 마이그레이션 러너는
  실패하면 그대로 멈추는 것이 의도된 설계이고, `pg_trgm`은 양쪽 환경에 확실히 있다
- **ORM 마이그레이션 도구를 쓰지 마라.** 이유: `CLAUDE.md`가 raw SQL만 허용한다
