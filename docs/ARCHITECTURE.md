# 아키텍처

## 시스템 개요

```
┌─────────────┐   ┌──────────────────────────────────────────────┐
│  Next.js UI  │──▶│  FastAPI (backend/app)                       │
└─────────────┘   │  문서 CRUD/버전 · 하이브리드 검색 · 시스템 상태 │
┌─────────────┐   └───────────────┬──────────────────────────────┘
│  MCP Server  │──(services 직접 재사용)──┐
└─────────────┘                   ▼      ▼
                  ┌──────────────────────────────────────────────┐
                  │  OpenProxy  (VRRP VIP : 6432)                 │
                  │  커넥션 풀링 · Primary 추적 · 재연결            │
                  └───────────────┬──────────────────────────────┘
                                  ▼
                  ┌──────────────────────────────────────────────┐
                  │  openSQL 클러스터 (PostgreSQL 17 + pgvector)  │
                  │  documents ──AFTER trigger──▶ embedding_jobs  │
                  │  (같은 트랜잭션에 잡 기록 = 트랜잭셔널 아웃박스) │
                  │           └──AFTER trigger──▶ document_links  │
                  │              (본문의 [[제목]] — 벡터 불필요)    │
                  │  document_chunks (vector(1024), HNSW)         │
                  │           └──청크 교체와 같은 트랜잭션──▶       │
                  │              document_edges (저장된 관계)      │
                  │  pg_notify('embedding_jobs') — 커밋 시 발행    │
                  └───────────────┬──────────────────────────────┘
                                  │ 5초 폴링 (주 경로)
                                  │ + LISTEN/NOTIFY (최적화, 선택)
                  ┌───────────────▼──────────────────────────────┐
                  │  Embedding Worker (python -m app.worker)      │
                  │  SKIP LOCKED claim → 청킹 → 임베딩 →           │
                  │  해시 재확인 + 청크 교체 + job done (단일 트랜잭션)│
                  └──────────────────────────────────────────────┘
```

> **다섯 구분으로 읽기 (설명용 개념 모델, ADR-031)**: 위 다이어그램에서 Next.js UI·MCP
> Server·REST API가 **Interface**(같은 services를 소비하는 대등한 인터페이스), 업로드
> API에서 `create_document`까지가 **Ingestion**, 트리거와 Embedding Worker가 **Processing**,
> openSQL 클러스터가 **Storage**, 검색·관계 조회 서비스가 **Retrieval**이다. 공식 용어가
> 아니며 기존 "DB 계층"·커밋 스코프 어휘를 대체하지 않는다.

**관계는 두 갈래로 만들어지고 시점이 다르다.** `document_links`는 본문이 바뀌는 즉시(벡터 불필요), `document_edges`는 청크가 교체되는 트랜잭션 안에서 생긴다. 둘 다 **DB 계층**이 만들며 애플리케이션은 읽기만 한다 — 관련 문서·태그 추천이 조회 시점 벡터 계산을 그만둔 근거다 (ADR-029 결정 5, ADR-030).

핵심 프레이밍: **잡 생성·코얼레싱·삭제 정합성은 전부 DB 안**(트리거 함수, 파셜 유니크 인덱스, FK CASCADE)에서 보장된다. 워커는 "DB가 만들어 둔 잡을 집어가는 무상태 실행기"이며, DB 밖 연산은 임베딩 모델 추론뿐이다.

> **기동(전달) 방식은 정합성의 일부가 아니다.** 워커가 잡을 언제 집어가든 — NOTIFY로 즉시든 폴링으로 5초 뒤든 — 잡이 유실되거나 중복 처리되지 않는 것은 아웃박스 테이블과 `SKIP LOCKED`가 보장한다. 그래서 `LISTEN`/`NOTIFY`가 OpenProxy를 통과하지 못해도 이 설계의 핵심 주장은 무너지지 않는다 (ADR-009).

## 디렉토리 구조

```
OpenArchive/
├── docker-compose.yml            # 로컬 개발용 pgvector 컨테이너
├── scripts/check.sh              # 통합 검증 (backend lint+test, frontend lint+test+build)
├── examples/
│   └── ingest_text.py            # 표준 라이브러리만 쓰는 독립 HTTP 텍스트 공급 예제
├── backend/
│   ├── pyproject.toml            # fastapi, psycopg[binary,pool], pydantic-settings, mcp<2, pypdf, python-docx / [dev]: pytest, ruff / [local]: sentence-transformers
│   ├── migrations/               # 001~013: extensions, tables, triggers, indexes,
│   │                             #   trgm, edges(006~008), auth(009), links(010~011), token(013)
│   ├── app/
│   │   ├── main.py               # FastAPI 앱 조립
│   │   ├── config.py             # pydantic-settings (DATABASE_URL, EMBEDDING_PROVIDER 등)
│   │   ├── db.py                 # AsyncConnectionPool만 — import 시 부작용 없음
│   │   ├── migrations.py         # 마이그레이션 러너 — API 서버 startup에서만 호출
│   │   ├── api/                  # 라우터: documents, search, system, auth, admin,
│   │   │                         #   diagnostics, clusters, retry (+ deps, schemas)
│   │   ├── services/             # parsing, chunking, documents, search, related,
│   │   │                         #   links, diagnostics, clusters, auth, system, visibility
│   │   ├── embeddings/           # base.py(Protocol), local.py(bge-m3), fake.py
│   │   └── worker.py             # 임베딩 워커 진입점
│   ├── mcp_server/server.py      # FastMCP stdio 서버 — search_documents, get_document, list_documents
│   └── tests/                    # test_chunking.py, test_triggers.py, test_worker.py, test_search_api.py ...
└── frontend/
    └── src/
        ├── app/                  # /(목록+업로드), /documents/[id], /search, /login,
        │                         #   /diagnostics, /clusters, /admin/status, /admin/users
        ├── components/
        ├── types/
        └── lib/                  # API 클라이언트 (fetch 래퍼)
```

`services/visibility.py`의 `VISIBLE_TO_USER`는 **모든 조회 경로가 공유하는 단일 열람 술어**다. 검색·관련 문서·그래프 순회·집계·위키링크 해석이 각자 조건을 쓰면 한 곳만 빠져도 비공개 문서가 새어 나간다 (ADR-018, ADR-027).

MCP 서버는 `app.services`를 직접 재사용한다. `search_documents`는 발췌(`excerpt`)·출처(`document_id`, `title`, `filename`)·기준 버전(`based_on_version`)을 반환하고, `get_document`는 추출 텍스트와 텍스트 버전·청크 상태를, `list_documents`는 접근 가능한 문서 메타데이터를 반환한다. 사용자 컨텍스트는 툴 인자가 아니라 `MCP_USER_ID` 환경변수로 고정하며, 미설정 시 public 문서만 조회한다 (ADR-025).

`POST /api/search`도 같은 근거 필드(`filename`·`based_on_version`)를 함께 내려준다. 서비스가 하나여도 두 경로의 응답 스키마가 갈라지면 "REST와 MCP의 결과가 같다"가 깨진다 — `tests/test_mcp_server.py`가 두 응답을 직접 비교해 이를 지킨다.

## DB 스키마

```sql
-- documents: 정형 메타데이터 + 버전 + 권한 (하이브리드 활용의 절반)
CREATE TABLE documents (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title            text NOT NULL,
  filename         text,                   -- 업로드된 원본 파일명 (출처 표시용). 파일 자체는 보관하지 않는다
  content_type     text NOT NULL,          -- pdf | docx | txt | md
  content          text NOT NULL,          -- 문서 텍스트 (현재 버전). 편집·버전 관리·임베딩의 대상
  content_hash     text NOT NULL,          -- sha256, 트리거의 변경 감지 기준
  version          int  NOT NULL DEFAULT 1,
  owner_id         text NOT NULL,
  visibility       text NOT NULL DEFAULT 'public',  -- public | private
  tags             text[] NOT NULL DEFAULT '{}',
  embedding_status text NOT NULL DEFAULT 'pending', -- pending|processing|ready|error
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),

  -- 텍스트를 추출하지 못한 문서(스캔 이미지 PDF 등)가 저장되는 것을 DB에서 차단한다.
  -- 빈 본문은 임베딩할 것이 없어 검색에 영원히 잡히지 않는 유령 행이 된다.
  -- 제거 문자를 명시한다: btrim의 1인자 형태는 공백만 제거해 탭·개행만 남은 본문이
  -- 그대로 통과하는데, 스캔 이미지 PDF의 추출 결과가 정확히 그 형태다 (M1에서 실측).
  CONSTRAINT documents_content_not_blank CHECK (length(btrim(content, E' \t\r\n\f')) > 0)
);

-- document_versions: 문서 텍스트의 버전 이력 (append-only)
-- 파일 버전 이력이 아니다. v1으로 되돌려도 원본 파일이 아니라 v1 시점의 문서 텍스트가 나온다.
CREATE TABLE document_versions (
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version      int  NOT NULL,
  content      text NOT NULL,
  content_hash text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (document_id, version)
);

-- document_chunks: 현재 버전의 청크만 유지 (인덱스 소형화 + 정합성 단순화)
CREATE TABLE document_chunks (
  id          bigserial PRIMARY KEY,
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version     int  NOT NULL,   -- 이 청크가 만들어진 기준 문서 버전 (아래 설명)
  chunk_index int  NOT NULL,
  content     text NOT NULL,
  embedding   vector(1024) NOT NULL,
  UNIQUE (document_id, chunk_index)
);

-- embedding_jobs: 트랜잭셔널 아웃박스 겸 작업 큐
CREATE TABLE embedding_jobs (
  id              bigserial PRIMARY KEY,
  document_id     uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  status          text NOT NULL DEFAULT 'pending', -- pending|processing|done|error
  attempts        int  NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  last_error      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  started_at      timestamptz,
  finished_at     timestamptz
);

-- 핵심: 문서당 pending 잡은 1개만 — DB 계층 코얼레싱
CREATE UNIQUE INDEX uq_pending_job_per_doc
  ON embedding_jobs(document_id) WHERE status = 'pending';

-- document_edges: 저장 시점에 만드는 관계 그래프 (006, ADR-029)
CREATE TABLE document_edges (
  src_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  dst_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kind            text NOT NULL,   -- overlaps|points_to|broader|related
  src_chunk_index int,             -- 위치가 의미 있는 관계만 채운다
  dst_chunk_index int,
  score           real NOT NULL,   -- ★ 척도가 kind마다 다르다 (아래)
  CONSTRAINT document_edges_not_self CHECK (src_document_id <> dst_document_id)
);

-- document_links: 위키링크. 대상 id가 아니라 제목을 저장한다 (010, ADR-030)
CREATE TABLE document_links (
  src_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  src_chunk_index int,
  target_title    text NOT NULL    -- ★ 해석은 조회 시점에, 조회자의 열람 범위에서
);

-- users·sessions: 최소 로그인 (009)
CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username      text NOT NULL UNIQUE,
  password_hash text NOT NULL,     -- hashlib.scrypt 결과만 저장한다
  is_admin      boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sessions (
  token      text PRIMARY KEY,     -- secrets.token_urlsafe(32)
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);

-- api_tokens: 프로그램용 장수명 위임 자격증명 (013, ADR-034)
CREATE TABLE api_tokens (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name       text NOT NULL,
  token_hash text NOT NULL UNIQUE,   -- sha256(원문). 원문은 발급 응답에만 반환
  scope      text NOT NULL CHECK (scope IN ('read', 'read_write')),
  created_at timestamptz NOT NULL DEFAULT now()
);
```

설계 근거: 잡은 콘텐츠 페이로드 없이 "이 문서는 재임베딩이 필요하다"는 신호만 담는다. 워커가 처리 시점에 `documents`의 최신 content를 읽으므로 (a) 연속 수정이 자연스럽게 코얼레싱되고 (b) 재처리가 최신 상태로 수렴하는 멱등 구조가 된다.

**관계 두 종류를 한 테이블에 넣지 않았다.** `document_edges`의 노드는 항상 **문서 id**이고 벡터가 준비된 뒤 트리거가 만든다. `document_links`의 대상은 **제목 문자열**이라 벡터가 필요 없고 본문이 바뀌는 순간 만들어지며, 대상 문서가 없어도 저장된다(깨진 링크는 위키의 정상 기능). 한 테이블로 합치면 `dst_document_id NOT NULL`이 깨지고 링크가 불필요하게 임베딩을 기다린다 (ADR-030).

**두 테이블 모두 `UNIQUE`에서 `NULL` chunk_index를 `-1`로 접는다.** PostgreSQL의 일반 `UNIQUE`는 `NULL`을 서로 다른 값으로 보므로, 위치 없는 관계가 여러 번 저장되는 것을 그대로 두면 중복이 쌓인다 (`COALESCE(src_chunk_index, -1)`).

### `document_chunks.version`의 용도

워커는 청크를 쓸 때 **처리 기준이 된 문서 버전**을 함께 기록한다. 이 컬럼은 세 곳에서 쓰인다:

1. **정합성 검증 쿼리** — 이 과제의 핵심 주장을 직접 증명하는 쿼리다:
   ```sql
   -- 원본과 벡터가 어긋난 문서를 찾는다. 파이프라인이 정상이면 항상 0건으로 수렴한다.
   SELECT d.id, d.title, d.version AS doc_version,
          c.version AS chunk_version, d.embedding_status
   FROM documents d
   JOIN document_chunks c ON c.document_id = d.id
   WHERE c.version <> d.version
   GROUP BY d.id, d.title, d.version, c.version, d.embedding_status;
   ```
   재임베딩이 진행 중일 때만 행이 나타나고, 완료되면 사라진다. **"원본-벡터 정합성이 유지된다"를 말이 아니라 쿼리로 보여줄 수 있다** — `/admin/status`와 데모에서 사용한다.

2. **문서 상세 화면** — "현재 검색 인덱스는 v3 기준" 표시
3. **디버깅** — 청크가 어느 버전에서 왔는지 추적

## 벡터 인덱스

```sql
CREATE INDEX idx_chunks_embedding ON document_chunks
  USING hnsw (embedding vector_cosine_ops);  -- m=16, ef_construction=64 기본값
```

- HNSW 선택 근거는 ADR-002. 코사인 거리(`<=>`)는 BGE-M3의 정규화 임베딩과 맞음.
- 필터 결합 검색의 "결과 부족" 문제는 검색 트랜잭션에서 `SET LOCAL hnsw.ef_search = 200`으로 완화한다 (ADR-011). pgvector 버전에 무관하게 동작한다.
- `SET LOCAL hnsw.iterative_scan = relaxed_order`도 **쓸 수 있다** — 0.8+를 요구하는데 배포판이 0.8.1이다. 다만 **켜지 않는다**: ADR-011 보강 3이 실측 없이 켜지 않기로 정했고, `backend/tests/test_indexes.py`가 그 선택을 근거와 함께 고정한다.

> **HNSW 가용성은 확정됐다.** 배포판에 pgvector **0.8.1**이 번들되어 있고(`docs/OPENSQL_RESEARCH.md` §0), 실 VM에서 `CREATE INDEX ... USING hnsw`와 검색 계획의 인덱스 사용을 실측했다(§12). ADR-002가 대비해 둔 pgvectorscale·IVFFlat 전환 경로는 쓰지 않는다.

## 자동 임베딩 파이프라인 (DB 계층)

### 트리거

```sql
CREATE FUNCTION on_document_content_changed() RETURNS trigger AS $$
BEGIN
  -- (1) 버전 이력 기록 — INSERT의 v1도 포함해 append-only 이력을 완성한다
  INSERT INTO document_versions (document_id, version, content, content_hash)
  VALUES (NEW.id, NEW.version, NEW.content, NEW.content_hash)
  ON CONFLICT (document_id, version) DO NOTHING;

  -- (2) 임베딩 대기 상태로 전환
  UPDATE documents SET embedding_status = 'pending'
   WHERE id = NEW.id AND embedding_status <> 'pending';

  -- (3) 잡 생성 — 파셜 유니크 인덱스가 코얼레싱 수행
  INSERT INTO embedding_jobs (document_id) VALUES (NEW.id)
    ON CONFLICT DO NOTHING;

  -- (4) 워커 깨우기 — 최적화이며 유실돼도 폴링이 처리한다 (ADR-009)
  PERFORM pg_notify('embedding_jobs', NEW.id::text);
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_documents_content_changed
  AFTER INSERT OR UPDATE OF content_hash ON documents
  FOR EACH ROW
  WHEN (pg_trigger_depth() = 0)          -- 트리거 내부 UPDATE로 인한 재귀 방지
  EXECUTE FUNCTION on_document_content_changed();
```

**버전 이력도 DB 계층이 책임진다.** 애플리케이션은 `document_versions`에 직접 INSERT하지 않는다 — `embedding_jobs`와 같은 원칙이다.

- **INSERT 시**: `version=1` 행이 이력에 기록된다. 문서 생성 직후부터 v1 조회가 가능하다.
- **PUT 시**: API는 `documents`의 `version`(+1), `content`, `content_hash`만 UPDATE한다. 이력 기록은 트리거가 **같은 트랜잭션에서** 수행하므로, 본문만 바뀌고 이력이 누락되는 상태가 구조적으로 불가능하다.
- `ON CONFLICT (document_id, version) DO NOTHING`은 재실행 안전장치다. 같은 버전 번호로 트리거가 두 번 발화해도 이력이 중복되지 않는다.

### 워커 처리 루프

**기동**: 5초 주기 폴링이 **주 경로**. `LISTEN embedding_jobs` 수신은 폴링을 앞당기는 **최적화**이며, 동작하지 않아도 파이프라인은 정상 작동한다 (ADR-009).

1. 폴링 틱 또는 NOTIFY 수신 시 — 잡을 claim하고 **즉시 커밋**:
```sql
UPDATE embedding_jobs j
   SET status='processing', attempts=attempts+1, started_at=now()
 WHERE j.id = (SELECT id FROM embedding_jobs
                WHERE status='pending' AND next_attempt_at <= now()
                ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
RETURNING j.id, j.document_id;

-- 같은 트랜잭션에서 문서 상태도 processing으로 (UI 표시용)
UPDATE documents SET embedding_status='processing'
 WHERE id = %(document_id)s AND embedding_status <> 'processing';
```

2. 문서의 최신 `content`와 **`content_hash`를 함께 읽기** → 청킹 → 임베딩
   *DB 밖 연산은 이 단계뿐이며, 시간이 오래 걸린다.*
   `version`은 여기서 읽지 않는다 — 3번에서 잠금을 잡은 뒤 읽는다 (아래 설명).

3. **단일 트랜잭션**으로 결과 반영 — 단, **읽었던 `content_hash`를 재확인**한다:
```sql
BEGIN;
  -- 처리 중 문서가 또 수정됐는지 확인. 다르면 이 결과는 낡았으므로 폐기.
  -- 청크에 기록할 version도 이 잠금 아래에서 함께 읽는다.
  SELECT content_hash, version FROM documents WHERE id = %(doc_id)s FOR UPDATE;
  -- content_hash <> 읽었던 값이면 → 아무것도 쓰지 않고 job만 done으로 마감하고 COMMIT
  --   (여기까지 쓴 것이 없어 ROLLBACK과 결과가 같다. 되돌리면 done 마감까지 날아간다)
  --   (새 pending 잡이 이미 생성돼 있으므로 최신 내용으로 다시 처리된다)

  DELETE FROM document_chunks WHERE document_id = %(doc_id)s;

  -- version을 명시적으로 채운다. 위에서 읽은 값을 그대로 쓴다.
  INSERT INTO document_chunks (document_id, version, chunk_index, content, embedding)
  VALUES (%(doc_id)s, %(locked_version)s, %(idx)s, %(chunk_text)s, %(vec)s);

  UPDATE documents SET embedding_status='ready' WHERE id = %(doc_id)s;
  UPDATE embedding_jobs SET status='done', finished_at=now() WHERE id = %(job_id)s;
COMMIT;
```

> **`document_chunks.version`은 반드시 3번의 `FOR UPDATE` 아래에서 읽은 값으로 채운다.**
> 이 컬럼은 정합성 검증 쿼리(`c.version <> d.version`)와 `/admin/status` 카운터의 근거다. **잘못 채우면 카운터가 영원히 0이거나 영원히 0이 아니게 되어 지표 자체가 무의미해진다.**
>
> 2번에서 미리 읽지 않는 이유: 본문이 `A → B → A`로 되돌아온 경우 `content_hash`는 원래대로 돌아오지만 `version`은 2 올라가 있다. 해시 재확인은 통과하는데 2번에서 읽은 `version`은 낡은 값이 된다. 잠금 아래에서 읽으면 이 경우에도 잠금 시점의 `version`이 정확히 기록된다.

4. 실패 시: `attempts` 기반 지수 백오프로 `next_attempt_at` 갱신 후 `pending` 복귀. `attempts`는 claim 시점에 이미 올라 있으므로 **3회를 소진하면**(3회째 실패) job `error` + `documents.embedding_status='error'`.

5. 좀비 회수: `processing` 상태로 설정된 임계(`ZOMBIE_TIMEOUT_MINUTES`, 기본 5분)를 초과한 잡을 `pending`으로 리셋한다. `sweep_zombies()`는 워커 신원으로 거르지 않는 **전역 스윕**이라 다른 워커가 남긴 좀비도 회수한다. 스윕은 워커 **루프 머리**에 있어 첫 반복이 곧 기동 시 1회 스윕이며, 워커가 재기동되면 즉시 실행된다. 별도의 기동 시 스윕이 따로 있는 것은 아니다. `attempts`는 초기화하지 않는다 — 초기화하면 계속 죽는 잡이 영원히 재시도되어 재시도 상한이 무의미해진다. 값 `0`은 단일 워커 복구 데모에서만 사용한다.

   남는 한계는 상태 표시다. 워커가 죽어 있는 동안 `/api/system/status`의 `jobs.processing`은 방치된 잡을 계속 세므로, **처리 중이 아닌 잡이 처리 중으로 보인다.** 재기동 후 첫 반복에서 회수되므로 데이터 문제는 아니지만, 워커가 내려간 구간에는 화면이 사실과 다르다.

> **4번과 5번에는 공통 예외가 있다: 그 문서에 새 `pending` 잡이 이미 있으면 `pending`으로 되돌리지 않고 `done`으로 마감한다.**
>
> 처리 중 문서가 수정되면 트리거가 새 잡을 만든다. 이때 실패한 잡이나 좀비 잡까지 `pending`으로 되돌리면 **문서당 pending 1건**을 강제하는 `uq_pending_job_per_doc` 위반이 되어 **워커가 죽는다.** 코얼레싱 제약과 재시도 로직이 만나는 지점이며, 파셜 유니크 인덱스를 둔 이상 구조적으로 발생한다.
>
> 확인과 복귀 사이의 경쟁을 없애려면 **문서 행을 `FOR UPDATE`로 잠근 뒤** 판단한다. 잡 생성은 전부 문서 변경 트리거 안에서 일어나므로 이 잠금이 새 잡의 커밋을 막는다. 잠그지 않으면 READ COMMITTED의 statement 스냅샷 탓에 방금 커밋된 새 잡을 놓치고, 그 `pending` 복귀가 유니크 제약 위반으로 터진다. 마감해도 유실이 아니다 — 최신 내용은 새 잡이 처리하며, 이는 3번에서 낡은 결과를 폐기하면서도 잡을 `done`으로 마감하는 것과 같은 원리다.
>
> **이 예외는 4번의 재시도 소진 판정보다 먼저 본다.** 낡은 내용을 보던 잡의 수명이 끝난 것이지 문서가 실패한 것이 아니므로, 소진 시점이라도 `documents.embedding_status`를 `error`로 떨어뜨리지 않는다. 순서를 뒤집으면 새 잡이 곧 `ready`로 되돌릴 문서에 거짓 `error` 배지가 뜬다.

> **3번의 `content_hash` 재확인이 멀티 워커 정합성의 핵심이다.**
> 워커A가 job1을 처리하는 도중 문서가 수정되면 새 pending job2가 생기고, 워커B가 이를 즉시 claim할 수 있다. 두 워커가 같은 `document_id`의 청크를 동시에 교체하면 **완료 순서에 따라 낡은 버전이 최종 상태로 남을 수 있다.**
> `FOR UPDATE` + 해시 비교로 "내가 읽은 내용이 아직 최신인가"를 커밋 직전에 확인하면, 낡은 결과는 스스로 폐기되고 최신 내용으로 수렴한다.

### 정합성 보장

| 보장 | 방식 |
|---|---|
| 원자성 | 문서 변경과 잡 기록이 같은 트랜잭션 (트랜잭셔널 아웃박스). 문서만 커밋되고 잡이 유실되는 경우가 구조적으로 불가능 |
| 전달 보장 | **주기 폴링이 주 경로**이므로 전달은 구조적으로 보장된다. `pg_notify`(커밋 시에만 발행)는 지연을 줄이는 최적화일 뿐, 유실돼도 다음 폴링 틱이 처리한다 |
| 코얼레싱 | 파셜 유니크 인덱스로 문서당 pending 1개. 처리 중(`processing`) 재수정되면 새 pending이 생긴다 |
| **최신 수렴** | 워커가 커밋 직전 `content_hash`를 `FOR UPDATE`로 재확인한다. 처리 중 문서가 바뀌었으면 그 결과를 폐기하므로, **멀티 워커가 경쟁해도 낡은 청크가 최종 상태로 남지 않는다** |
| 삭제 정합성 | `ON DELETE CASCADE`로 청크·잡이 문서와 원자적으로 삭제. 워커 개입 불필요. 처리 도중 문서가 삭제되면 워커의 최종 트랜잭션이 0건 갱신으로 끝남 — 무해 |
| **버전 일관성** | 활성 청크는 **어느 시점에 조회해도 하나의 버전**이며 여러 버전이 섞이지 않는다. 청크 교체가 단일 트랜잭션(`DELETE`+`INSERT`)이라 다른 세션은 커밋 전후만 보고, 검색·관련 문서 쿼리가 모두 **단일 문**이라 READ COMMITTED의 문 단위 스냅샷 안에서 일관된다. `document_chunks.version`이 "지금 검색되는 것이 몇 번 버전인가"를 항상 답할 수 있게 한다 |
| 검색 공백 없음 | 재임베딩 중에도 결과가 비지 않는다 — **이전 버전 청크가 그대로 검색된다** (검색 쿼리가 `embedding_status`로 거르지 않기 때문 — 검색 데이터 흐름 절 참조). 위 버전 일관성과 짝을 이룬다: 공백이 없고, 그때 나오는 것은 낡았을지언정 **일관된 한 버전**이다 |
| 읽기 정합성 | 검색을 plain `BEGIN`으로 감싸 OpenProxy가 Primary로 라우팅하게 강제한다. 복제 지연으로 방금 임베딩된 청크가 누락되지 않는다 (ADR-010) |
| 멱등성 | 청크 교체가 delete+insert라 잡 재실행의 종착 상태가 항상 동일 |

> **이 표는 즉시 반영을 보장하지 않는다.** 재임베딩 중에는 이전 버전이 검색되고, 폴링 주기(5초)와 임베딩 소요만큼 반영이 늦으며, Failover 구간에는 요청이 실패한다. 우리가 보장하는 것은 **버전 일관성**과 **최신으로의 수렴**이며, 그 사이의 어긋난 구간은 정합성 검증 쿼리로 **관측할 수 있다**. 사용자 대상 문구에서 쓰지 않을 표현은 **ADR-015가 문자 그대로 열거한다** — 이 문서에서 되풀이하지 않으니 그쪽을 근거로 삼는다.

## 고가용성(HA) 전략

### OpenSQL 클러스터 구성 (공식 권장 3노드)

```
                    ┌──────────────────────────────┐
   애플리케이션 ───▶│  VRRP VIP  (OpenProxy)       │  ← 단일 엔드포인트
   (API · 워커)     └──────────┬───────────────────┘
                               │ openproxy.toml의 shards에 노드 선언
                               │ use_patroni=true → 역할은 Patroni가 관리
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌──────────┐    ┌──────────┐    ┌──────────┐
        │ Node 1   │    │ Node 2   │    │ Node 3   │
        │ PG17     │    │ PG17     │    │ PG17     │
        │ OpenHA   │    │ OpenHA   │    │ OpenHA   │
        │          │    │ OpenProxy│    │ OpenProxy│
        └────┬─────┘    └────┬─────┘    └────┬─────┘
             └───────────────┼───────────────┘
                             ▼
                    ┌─────────────────┐
                    │ OpenHA DCS      │  etcd v3
                    │ 멤버십·역할 저장 │
                    └─────────────────┘
```

### 책임 분리 — 무엇을 OpenSQL이 하고, 무엇을 우리가 하는가

| 책임 | 주체 |
|---|---|
| 노드 장애 감지, 새 Primary 선출·승격 | **OpenHA Cluster Manager (Patroni)** |
| PostgreSQL 프로세스 장애 감지·재기동 | **OpenHA Cluster Manager (Patroni)** |
| 클러스터 상태 공유 | **OpenHA DCS (etcd)** |
| 커넥션 풀링, 백엔드 축출 후 재연결 | **OpenProxy** |
| Primary 변경 감지 후 재연결, VIP 이중화 | **OpenProxy** — 단 이 설치에는 **구성되어 있지 않다** (아래 참조) |
| 미처리 잡의 무손실 보존 | **DB 계층** (`embedding_jobs`는 WAL 로깅 테이블 → 스탠바이 복제) |
| 연결 끊김 시 재시도, 잡 재개, 좀비 회수 | **애플리케이션 (우리)** |

> **`PROJECT_CONTEXT.md` 설계 원칙 준수**: 위 표에서 주체가 OpenSQL 컴포넌트인 **상위 5줄**은 OpenSQL이 제공하는 기능이므로 애플리케이션에서 중복 구현하지 않는다 (ADR-006).
>
> **5번째 줄은 설계이고 현재 구성이 아니다.** 이 설치의 `openproxy.toml`에는 `use_patroni`도 `[general.etcd]`도 없고 `servers`에 primary 하나가 하드코딩돼 있어, 현재 OpenProxy는 **정적 서버 목록을 가진 순수 커넥션 풀러**다. 노드가 1대라 발견할 대상 자체가 없으므로 제품 기능의 결함이 아니라 구성의 문제이며, HA로 전환할 때 Patroni·etcd 연동을 함께 설정해야 한다 (ADR-006 실측 정정, ADR-020 결정 3). 이 줄을 근거로 "새 Primary 발견을 시연했다"고 쓰지 않는다.

### 애플리케이션 접속

```bash
# 단일 엔드포인트. 멀티호스트 DSN·target_session_attrs 사용 안 함.
DATABASE_URL="postgresql://app@<vip>:6432/<pool_name>"
```

- `<pool_name>`은 `openproxy.toml`의 `[pools.<name>]` 이름이다 (DB 이름 자리에 pool 이름을 넣는 것이 OpenProxy 규약).
- 로컬 개발 시에는 같은 환경변수에 단일 컨테이너 주소를 넣는다. **코드는 로컬/클러스터를 구분하지 않는다.**

### 애플리케이션이 담당하는 복구 로직

- **API**: `psycopg_pool.AsyncConnectionPool(check=AsyncConnectionPool.check_connection)` — 죽은 연결을 대여 시점에 감지·폐기·재수립. 처리 도중 끊긴 요청은 미들웨어가 **1회 재시도**하되 대상은 **읽기 전용 요청**뿐이다(`GET`·`HEAD`·`POST /api/search`). 쓰기는 커밋 도달 여부를 구분할 수 없어 재시도 시 중복 생성 위험이 있다 (ADR-023).
- **워커 (잡 처리)**: 동일한 풀 정책. 처리 중 연결이 끊기면 트랜잭션이 롤백되고, 잡은 `processing` 상태로 남았다가 좀비 회수 스윕이 `pending`으로 되돌린다.
- **워커 (기동)**: **주기 폴링(5초)이 주 경로**다. `LISTEN`은 최적화이며, 연결이 끊기면 백오프 재연결 후 `LISTEN`을 재등록한다. **LISTEN이 아예 동작하지 않아도 파이프라인은 정상 작동한다** (ADR-009).
- **잡 큐 내구성**: `embedding_jobs`는 일반 WAL 로깅 테이블이므로 스탠바이에 복제된다. Failover 후 미처리 잡이 새 Primary에 그대로 존재하고, 워커 재연결 즉시 재개된다.

복구 시나리오는 실행 코드와 실측 문서로 나누어 검증한다.

**① PostgreSQL 프로세스 장애 — 실행 코드.** `scripts/demo_recovery.sh`는 마이그레이션이 적용된 실 OpenSQL VM을 전제로 한다. postmaster 부모 프로세스에 `SIGKILL`을 한 번 보내고, Patroni의 자동 재기동 → 기존 앱 연결의 `OperationalError` 계열 예외 → OpenProxy 재접속 → 미처리 잡 재개 → 정합성 카운터 0 수렴을 단일 타임라인으로 확인한다. API와 워커는 스크립트가 `FakeProvider`로 직접 기동한다.

**타임라인의 시각 출처를 섞지 않는다.** Patroni 사건(`Postgresql is not running`, `starting primary after failure`)의 경과는 **로그가 스스로 적은 시각**에서 계산한다 — ssh 폴링이 성공한 시각을 쓰면 폴링 주기와 왕복 지연이 그대로 값이 되어 §0의 46 ms 급 사건이 초 단위로 부풀려진다. 그래서 t0는 VM 시계로도 받아둔다(같은 시계끼리만 뺄셈해 시계 차이를 지운다). 로그 사건은 소급 계산이 가능하므로 **앱 관측(재접속·잡 재개·정합성)을 먼저 재고 로그를 나중에 읽는다** — 순서를 반대로 하면 로그 대기 시간이 재접속 시각에 더해져 `t(재접속) ≥ t(재기동 로그)`가 구조적으로 보장돼 버린다. 앱 관측값인 재접속 시각은 PostgreSQL이 접속을 수락한 시점이 아니라 앱이 재접속에 성공한 시점이므로 §0의 5.85초보다 폴링 주기만큼 뒤에 온다.

```bash
OPENSQL_HOST=<vm-ip> \
OPENSQL_SSH=<ssh-host> \
DATABASE_URL="postgresql://postgres:pg_password@<vm-ip>:6432/opensql" \
PATRONI_URL="http://<vm-ip>:8008" \
PATRONI_LOG="/home/opensql/logs/patroni.log" \
API_PORT=18000 \
bash scripts/demo_recovery.sh
```

**② etcd 정지 — 실측 문서만 유지.** etcd를 99초 정지했을 때 `failsafe_mode=true`가 primary 강등을 막아 앱은 전 구간 아무것도 눈치채지 못했고 6432·5432 쓰기가 계속 가능했다. 즉 DCS 장애는 곧 서비스 장애가 아니다.

**③ Patroni 정지 — 실측 문서만 유지.** Patroni만 `SIGKILL`해도 PostgreSQL은 계속 쓰기를 받았지만, etcd의 리더 키는 23.9초에 소멸했고 106초 관측 동안 아무것도 Patroni를 되살리지 않았다. 이 설치의 systemd 유닛이 `opensql-etcd.service` 하나뿐이고 Patroni·PostgreSQL·OpenProxy는 `nohup` 맨 프로세스라는 구성과 일치한다.

②·③은 시연 시간에 비해 핵심 서사를 분산시키므로 코드로 만들지 않았다. 실측 조건과 타임라인은 `OPENSQL_RESEARCH.md` §0 「Single 장애 주입 실측」에 남긴다.

**DB 프로세스 장애 자동 복구를 검증했다. 노드 사망은 복구되지 않으며, 이는 사무국 지시에 따른 Single 구성의 제약이다. HA 설계는 유지하되 노드 승격은 검증하지 못했고, 애플리케이션 측 재연결·잡 재개·정합성 수렴을 함께 검증했다.**

검증 대상이 잡 큐와 정합성이라 임베딩 품질은 무관하다. 그래서 스크립트가 `.env` 설정과 무관하게 **`EMBEDDING_PROVIDER=fake`를 고정**한다 — BGE-M3 로딩 시간이 복구 시나리오의 타임아웃 여유를 잠식하기 때문이다. 실 모델로 재현하려면 스크립트를 고쳐야 한다.

### Failover 시간 특성

OpenSQL이 배포하는 `patroni.yml` 기준값:

| 파라미터 | 값 |
|---|---|
| `ttl` | 30초 |
| `loop_wait` | 10초 |
| `retry_timeout` | 10초 |
| `maximum_lag_on_failover` | 1MB |
| `failsafe_mode` | `true` |

> **정확한 표현은 "짧은 중단 후 자동 복구"다.** 장애 감지부터 승격까지 수십 초가 걸린다. 사용자 관점에서는 그 구간의 요청이 실패하고 이후 정상 복구된다. 이 자리에서 쓰지 말아야 할 반대말은 **ADR-015와 ADR-020 결정 4가 문자 그대로 지정한다** — 이 문서에서 되풀이하지 않는다.

### 제약: `max_connections = 100`

OpenSQL `patroni.yml`의 PostgreSQL 파라미터는 `max_connections: 100`이다. API 풀 + 워커 잡 처리 풀 + 워커 LISTEN 연결이 모두 이 안에 들어가야 하며, OpenProxy의 `pool_size`와 함께 산정한다.

### 로컬 개발 갭

로컬은 pgvector 단일 컨테이너다 (ADR-007). **OpenProxy 경유 경로는 로컬에서 검증할 수 없다** — 공식 Docker 배포판이 없기 때문이다. M0 검증 목록은 `docs/OPENSQL_RESEARCH.md` §12를 따른다.

## API 설계

| 엔드포인트 | 내용 |
|---|---|
| `POST /api/documents` | multipart 업로드. pypdf/python-docx/plain 파싱 → INSERT. 여기서 트리거가 파이프라인을 자동 기동 — 임베딩 관련 코드 없음. **텍스트 추출 결과가 비면 400** (아래). **원본 파일은 보관하지 않는다** — 추출 텍스트만 저장하고 파일은 버린다 |
| `POST /api/documents/text` | JSON 텍스트 공급(`txt`·`md`). `filename`은 NULL이며, 파생 데이터는 업로드 경로와 동일하게 DB 트리거가 만든다. 빈 문서 텍스트와 500,000자 초과는 400 |
| `GET /api/documents` | 목록 + `status`/`tag` 필터, embedding_status 포함 |
| `GET /api/documents/{id}` | 상세 + 텍스트 버전 목록 + 청크 수 + 청크 기준 버전 |
| `PUT /api/documents/{id}` | 편집된 문서 텍스트(`{content, version}` JSON) → `version`+1, `content`, `content_hash` UPDATE. **버전 이력 기록과 재임베딩 잡 생성은 트리거가 수행.** 요청의 `version`이 현재 버전과 다르면 **409** (아래) |
| `PUT /api/documents/{id}/tags` | `{tags: string[]}`로 태그 전체 교체. 트리거는 `UPDATE OF content_hash`에만 걸려 있으므로 **재임베딩을 유발하지 않는다** |
| `DELETE /api/documents/{id}` | CASCADE로 벡터까지 원자 삭제 |
| `POST /api/documents/{id}/reembed` | **임베딩 실패 복구.** 아래 참조 |
| `GET /api/documents/{id}/related` | **관련 문서.** 저장된 관계(`document_edges`)를 읽는다 (ADR-018 개정 · ADR-029). 청크가 없으면 `not_indexed`, edge가 없으면 `no_edges` |
| `GET /api/documents/{id}/tag-suggestions` | **태그 추천.** 관계 이웃의 태그 빈도 (ADR-019). 청크가 없으면 `not_indexed` |
| `GET /api/documents/{id}/links` | **본문이 가리키는 위키링크.** 조회자의 열람 범위에서 해석하며, 대상이 없거나 보이지 않으면 `document_id: null` (ADR-030) |
| `GET /api/documents/{id}/backlinks` | **이 문서를 가리키는 문서.** 열람 가능한 출발 문서만 |
| `POST /api/search` | 하이브리드 검색 + 관계 순회 (아래) |
| `POST /api/auth/login` · `logout` · `GET /api/auth/me` | 최소 로그인. 세션 토큰은 `sessions` 테이블에 저장 |
| `POST /api/auth/tokens` · `GET /api/auth/tokens` · `DELETE /api/auth/tokens/{id}` | **세션 전용** API 토큰 발급·목록·폐기. 원문은 발급 응답에만 반환하며 기본 scope는 `read` |
| `GET /api/diagnostics` | **진단.** 고아 문서·깨진 링크·중복 후보 등을 **열람 범위 기준**으로 집계 (ADR-027) |
| `GET /api/clusters` | **주제 덩어리.** 관계 그래프의 연결 요소 |
| `GET /api/admin/users` 등 | 관리자 전용 |
| `GET /api/system/status` | **로그인 필요 · 운영/데모 전용**: `inet_server_addr()`(현재 접속 노드), pending/processing/error 잡 수, 임베딩 프로바이더명, **정합성 검증 쿼리 결과**(`c.version <> d.version` 건수). `/admin/status`가 소비하며 사용자 화면은 호출하지 않는다. SQL과 결과 모델은 `services/system.py`에 있고 라우터는 인증과 응답 변환만 맡는다 |

> **구현 현황 (M11-c 기준)**: 위 표 전체가 구현되어 있다. 파일 업로드와 JSON 텍스트 공급은 같은 INSERT 헬퍼와 DB 트리거 파생 계약을 공유한다. 프로그램은 사람이 발급한 `read_write` 위임 API 토큰으로 세션 쿠키 없이 텍스트를 공급할 수 있다 (ADR-034·035).
>
> **모든 조회에 열람 범위가 걸린다.** 검색·관련 문서·링크·백링크·진단 집계·클러스터가 같은 `VISIBLE_TO_USER` 술어를 쓴다. 볼 수 없는 문서는 자리 표시조차 남기지 않는다 — 표시 자체가 존재와 개수를 누출한다 (ADR-027).
>
> 새 파일로 교체하는 경로는 없다. 새 파일을 올리려면 업로드 후 이전 문서를 삭제해야 한다.
>
> 라우터는 얇다. 요청 검증과 상태 코드 변환만 하고 실제 로직은 `services/documents.py`·`services/search.py`·`services/system.py` 등에 있으며, MCP 서버가 문서·검색 서비스를 재사용한다. 도메인 예외를 상태 코드로 옮기는 매핑은 `main.py`의 exception handler 한 곳에 있다.

### 빈 파싱 결과 처리 (`POST /api/documents`)

파싱 결과가 공백 제거 후 빈 문자열이면 **400을 반환하고 저장하지 않는다.**

```
400 Bad Request
{ "detail": "문서에서 텍스트를 추출하지 못했습니다. 스캔 이미지 PDF는 지원하지 않습니다." }
```

저장 후 `error` 상태로 두는 대안을 택하지 않은 이유: 빈 문서는 임베딩할 것이 없어 **영원히 검색에 잡히지 않는 유령 행**이 되고, 사용자는 목록에서 실패 배지만 볼 뿐 원인을 모른다. 업로드 시점에 즉시 알리는 편이 낫다.
DB 계층에도 `CHECK (length(btrim(content, E' \t\r\n\f')) > 0)`를 두어 이중으로 막는다 (스키마 절 참조). 여기서 "공백 제거"는 **공백·탭·CR·LF·폼피드**를 뜻한다 — `btrim`의 1인자 형태는 공백만 제거하므로 개행뿐인 추출 결과를 걸러내지 못한다.

### 임베딩 실패 복구 (`POST /api/documents/{id}/reembed`)

3회 재시도 후 `error`가 된 문서를 다시 처리 대기로 되돌린다.

```sql
-- 애플리케이션은 embedding_jobs를 직접 건드리지 않는다.
-- content_hash를 SET 절에 언급하기만 하면 UPDATE OF 트리거가 발화한다
-- (값이 같아도 컬럼이 SET 절에 있으면 발화하는 것이 PostgreSQL 동작).
UPDATE documents SET content_hash = content_hash WHERE id = %(doc_id)s;
```

트리거가 상태를 `pending`으로 되돌리고 새 잡을 만든다. **잡 생성 책임이 DB 계층에 남는다** — `CLAUDE.md`의 "애플리케이션 코드에서 `embedding_jobs`에 직접 INSERT 하지 마라" 규칙을 우회하지 않는 유일한 방법이다.

버전은 올라가지 않으므로 이력이 오염되지 않고, 트리거의 `ON CONFLICT (document_id, version) DO NOTHING`이 중복 이력을 막는다.

### 인라인 편집과 낙관적 동시성 (`PUT /api/documents/{id}`)

문서는 재업로드 없이 고칠 수 있다. **편집 대상은 문서 텍스트이며 원본 파일이 아니다** (ADR-017).
업로드 문서에서는 그 텍스트가 추출 텍스트이고, 직접 공급 문서(`filename IS NULL`)에는 추출한
대상이 없다 — 거절 문구와 UI 레이블이 이 구분을 따른다 (ADR-035 결정 3).

```
PUT /api/documents/{id}
  body: { "content": "...", "version": 2 }

  version 불일치 → 409 Conflict
    { "detail": "다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요.",
      "current_version": 3 }

  version 일치  → 200
    UPDATE documents SET version = version + 1, content = ..., content_hash = ...
     WHERE id = %(id)s AND version = %(client_version)s;
    -- 0건 갱신이면 그 사이에 바뀐 것이므로 409로 되돌린다
```

- 버전 이력 기록과 재임베딩 잡 생성은 **트리거가 수행**한다. 이 핸들러는 `documents`만 UPDATE한다
- `WHERE ... AND version = %(client_version)s`로 비교와 갱신을 한 문장에 두어, 확인과 쓰기 사이의 경쟁을 없앤다
- 저장 직후 `embedding_status`가 `pending`으로 돌아가고, 정합성 카운터(`c.version <> d.version`)가 1 올랐다가 워커 처리 후 0으로 복귀한다. **이 흐름이 데모의 핵심 장면이다**

**원본 파일과 추출 텍스트를 구분한다.** 현재 스키마에 바이너리 컬럼이 없으므로, 편집 후에는 `filename = report.pdf`인데 `content`가 그 PDF의 추출 결과와 다른 상태가 될 수 있다. **결함이 아니라 설계된 동작**이며, 스캔 품질이 나쁜 PDF의 오추출을 고치는 정당한 용도가 있다. UI는 편집 영역을 "본문"이 아니라 **"추출 텍스트"**로 표기한다 — 원본 파일이 없는 문서에서는 **"문서 텍스트"**다 (`UI_GUIDE.md`).

## 검색 데이터 흐름

질의 텍스트 → 동일 프로바이더로 질의 임베딩 → **단일 `WITH RECURSIVE` SQL**. 벡터 후보 확보와 관계 순회가 한 쿼리 안에서 끝난다.

```sql
BEGIN;  -- ★ plain BEGIN. READ ONLY 금지 (아래 설명)

SET LOCAL hnsw.ef_search = 200;      -- 필터 통과 후보를 충분히 확보 (기본 40)
SET LOCAL random_page_cost = 1.1;    -- 무필터 검색이 HNSW를 타게 한다 (ADR-011 보강 5)

WITH RECURSIVE candidates AS (       -- ① 벡터 후보 (k * 5). 필터를 여기 안에 둔다
    SELECT c.document_id, c.chunk_index,
           ( … 앞뒤 청크를 이어붙인 발췌 … ) AS content,
           c.version, c.embedding <=> %(qvec)s::vector AS dist
    FROM document_chunks c JOIN documents d ON d.id = c.document_id
    WHERE (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)
      AND (%(ctype)s::text IS NULL OR d.content_type = %(ctype)s)
      AND (d.visibility = 'public' OR d.owner_id = %(user)s)
    ORDER BY c.embedding <=> %(qvec)s::vector
    LIMIT %(k)s * 5
),
resolved_links AS (                  -- ② 위키링크를 열람 범위에서 edge로 해석 (ADR-030)
    SELECT l.src_document_id, d.id AS dst_document_id, 'refers'::text AS kind, …
    FROM document_links l
    JOIN documents d ON d.title = l.target_title
                    AND (d.visibility = 'public' OR d.owner_id = %(user)s)
),
traversal_edges AS (                 -- ③ 저장된 관계 ∪ 해석된 링크
    SELECT … FROM document_edges
    UNION ALL
    SELECT … FROM resolved_links
),
walk AS (                            -- ④ 깊이 2까지 순회. 한 단계마다 거리에 +2.0
    SELECT …, 0 AS depth FROM candidates          -- 시작점
    UNION ALL
    SELECT …, w.dist + 2.0, w.depth + 1
    FROM walk w JOIN traversal_edges e ON e.src_document_id = w.document_id
    …                                             -- path 배열로 순환을 막는다
),
expanded AS (                        -- ⑤ 직전 텍스트 버전을 revision으로 더한다
    SELECT … FROM walk
    UNION ALL
    SELECT …, 'revision' FROM candidates JOIN document_versions …
),
deduplicated AS ( … ),               -- ⑥ 문서·버전·청크 단위로 1건
selected AS (                        -- ⑦ 직접 결과와 확장 결과를 각각 LIMIT k
    (SELECT * FROM deduplicated WHERE depth = 0 ORDER BY dist, … LIMIT %(k)s)
    UNION ALL
    (SELECT * FROM deduplicated WHERE depth > 0 ORDER BY dist, <kind 순서>, … LIMIT %(k)s)
)
SELECT … FROM selected hit JOIN documents d ON d.id = hit.document_id
ORDER BY CASE WHEN hit.depth = 0 THEN 0 ELSE 1 END,   -- 직접 결과가 항상 먼저
         hit.dist, <kind 순서>, hit.depth, …;

COMMIT;
```

> 실제 쿼리는 `services/search.py`의 `SEARCH_SQL` **한 곳에만** 존재하며 REST API와 MCP 서버가 공유한다. 위는 단계 구조만 옮긴 것이다 — 컬럼 목록과 순환 방지 `path` 배열은 코드를 보라. **문서에 전체 SQL을 복사해 두지 않는다**: 쿼리가 길어진 뒤로는 복사본이 조용히 낡아 잘못된 근거가 된다.

정형 필터·권한 술어·벡터 정렬·관계 순회가 한 쿼리에 결합된다(가산점 포인트).

**`<kind 순서>`는 동점 타이브레이커다** — `overlaps`(0) · `related`(1) · `refers`(2) · `revision`(3). 사람이 본문에 직접 쓴 링크가 같은 문서의 과거 판본보다 앞선다 (ADR-030).

**한 단계마다 거리에 `GRAPH_DISTANCE_PENALTY = 2.0`을 더한다.** 코사인 거리의 최대값이 2이므로, 관계로 닿은 문서가 직접 벡터 결과를 절대 앞지르지 못한다. `score = 1 - dist` 정렬 의미를 그대로 두면서 층을 가르는 방법이다.

### 네 가지 설계 결정이 이 쿼리에 반영되어 있다

**1. `BEGIN … COMMIT`으로 감싼다 (ADR-010)**

OpenProxy는 `query_parser_read_write_splitting` 활성 시 **트랜잭션 밖의 단순 `SELECT`를 Replica로 라우팅**하며, 복제 지연 보장이 없다. 워커가 Primary에 청크를 커밋한 직후 검색하면 방금 임베딩된 청크가 누락될 수 있다.

> ⚠️ **`BEGIN READ ONLY`를 쓰면 안 된다.** OpenProxy 1.1.3부터 `BEGIN READ ONLY`와 `START TRANSACTION READ ONLY`는 **의도적으로 Replica로 라우팅**된다. "읽기 전용이니 READ ONLY로 선언하는 게 맞다"는 직관을 따르면 정확히 반대 결과가 나온다.

**2. 두 개의 `SET LOCAL` — `hnsw.ef_search`와 `random_page_cost` (ADR-011 보강 4·5)**

`ef_search = 200`: HNSW 인덱스는 `document_chunks`에 있는데 필터는 JOIN 상대인 `documents`에 있다. 기본 `ef_search = 40`으로는 태그 필터가 조금만 좁아도 `LIMIT k`를 채우지 못한다. 후보 풀을 키워 이를 완화한다. `SET LOCAL`이므로 트랜잭션이 끝나면 자동 복원된다 — ①의 명시적 트랜잭션이 여기서 한 번 더 쓸모가 있다.

`random_page_cost = 1.1`: VM 기본값 4에서는 플래너가 HNSW를 아예 고르지 않는다. 힙이 3MB인데 인덱스가 47MB라, 임의 접근을 4배로 계산하면 통째로 읽는 쪽이 싸다고 나온다. **태그·유형 필터는 선택적**(`%(tags)s IS NULL OR …`)이므로 이 쿼리에는 필터 없는 경로가 항상 존재하며, 그 경로가 인덱스를 타느냐가 여기 달려 있다. 전역이 아니라 `SET LOCAL`로 거는 이유는 OpenProxy가 백엔드 반납 시 `RESET ALL`만 하고 `DISCARD ALL`은 하지 않아(§5-2) 세션 GUC에 의존하지 않는 편이 안전하기 때문이다.

> **무필터 검색 경로는 아직 직접 측정하지 않았다.** 같은 형태·같은 규모의 관련 문서 쿼리가
> `rpc=4`에서 Seq Scan 624ms, `rpc=1.1`에서 HNSW 33.8ms인 것에 근거한 적용이다
> (`OPENSQL_RESEARCH.md` §12 16번). 필터가 붙는 경로는 아래대로 어느 쪽이든 Seq Scan이라
> **걸어서 손해 볼 것이 없다.**

> **JOIN을 여기 둬도 된다 — 두 번 측정해 확인했다 (2026-08-05).** 1차 실측은 "벡터 정렬 서브쿼리에
> `documents` JOIN이 있으면 HNSW를 못 쓴다"로 읽었으나 **재측정에서 재현되지 않았다.** 플래너는
> 벡터 정렬을 인덱스로 처리하고 `documents`를 그 뒤에 nested loop로 붙인다 — 위 구조 그대로
> 인덱스를 쓴다 (`OPENSQL_RESEARCH.md` §12 17번, ADR-018 재개정).
>
> **다만 태그 필터가 붙으면 플래너가 Seq Scan을 고른다** — `random_page_cost`를 낮춰도 그렇다
> (6000행에서 232ms). 태그가 선택적일수록(500문서 중 84개) 좁혀 놓고 정렬하는 편이 실제로 싸기
> 때문이며, 이는 플래너의 합리적 판단이다. `ef_search`를 키우는 것은 **인덱스를 탈 때** 필터 통과
> 후보를 확보하기 위한 장치다.

**3. 문서당 1건으로 중복 제거 (ADR-011)**

긴 문서 하나가 상위 k를 청크로 도배하는 것을 막는다. 후보를 `k*5`로 넉넉히 뽑은 뒤 `DISTINCT ON (document_id)`으로 문서당 최고 점수 청크만 남기고, 최종적으로 거리순 `LIMIT k`를 적용한다. 사용자는 **문서 목록**을 받고, 각 문서에는 가장 잘 맞는 발췌가 붙는다.

**4. `embedding_status = 'ready'` 필터를 제거했다**

이전 설계는 `WHERE d.embedding_status = 'ready'`를 두었으나, 이는 PRD의 **"재임베딩 완료 전까지는 이전 벡터로 검색이 계속된다(검색 공백 없음)"**와 정면으로 모순됐다. 문서를 수정하면 트리거가 상태를 `pending`으로 되돌리므로, 재임베딩이 끝날 때까지 그 문서가 **검색에서 통째로 사라졌다.**

필터를 제거해도 안전한 이유:
- 신규 문서는 아직 청크가 없으므로 JOIN에서 자연히 제외된다 (상태 필터 불필요)
- 워커가 청크를 **단일 트랜잭션으로 교체**하므로, 어느 시점에 조회해도 청크 집합은 항상 일관된 한 버전이다
- 재임베딩 중에는 **이전 버전 청크**가 조회된다 — 이것이 PRD가 의도한 "검색 공백 없음"이다
- 임베딩 실패(`error`) 문서도 이전 청크로 계속 검색된다 — 사용자 관점에서 올바른 동작이다

`embedding_status`는 **검색 필터가 아니라 UI 상태 표시용**으로만 쓴다.

### 키워드 + 벡터 RRF는 검토 후 미채택 (ADR-016)

**검색 경로에 키워드 랭킹을 두지 않는다. 검색 코드에 RRF는 없다.** 원래는 "core 완성 후 여유가 있으면 착수하는 조건부 확장"이었고, m9에서 실측한 뒤 접었다. 여기에 계획 SQL을 남겨 두면 다음 사람이 그것을 근거로 되살리므로 **의도적으로 지웠다.** 측정 기록은 `OPENSQL_RESEARCH.md` §14 Step 3에 있다.

두 단계로 배제됐다.

**1단계 — `tsvector` 경로 (#29).** 번들 확장에 한국어 형태소 분석기가 없다. `simple` 파서는 조사를 분리하지 못해 `"OpenSQL의"`가 `opensql의`로 색인되고 `opensql`로 검색되지 않는다. 효과가 조사 없이 등장하는 토큰에만 한정된다.

**2단계 — `pg_trgm` 대안 (m9 step 3).** `tsvector` 대신 trigram으로 키워드 경로를 세워 실측했다. GIN 인덱스(1,384 kB)는 정상적으로 탔다 — **느려서 접은 것이 아니다.**

> **trigram은 포함 여부를 판정하는 이진 필터이지 랭킹 함수가 아니다.** `word_similarity`는 질의어가 content 안에 온전히 있으면 무조건 1.000을 준다. 실측에서 `OpenSQL` 후보 125개 중 **109개가 동점**이었고, 순위는 보조 정렬(`c.id`)이 정했다 — 사실상 무작위다. RRF는 순위를 입력으로 받는 알고리즘이라, 한쪽 입력이 무작위면 융합은 정보를 더하는 게 아니라 **잡음을 섞는 일**이 된다.

빈도·문서 길이 정규화(BM25 계열)가 있어야 순위가 생기는데 trigram으로는 만들 수 없다. **한국어에서 키워드 랭킹을 세우려면 형태소 분석기가 먼저이며, 그것이 이 결정의 진짜 제약이다.** `pg_trgm` 확장 자체는 관계 방향 실측의 재현 근거로 005 마이그레이션에 남지만 검색 경로에서는 쓰지 않는다.

## 관련 문서·태그 추천

문서 상세에서 두 가지를 제공한다. 둘 다 **저장된 관계(`document_edges`)를 읽으며, 조회 시점에 벡터를 계산하지 않는다** (ADR-018 개정 · ADR-029 결정 5).

> **2026-08-11에 방식이 바뀌었다.** 원래는 대상 문서의 `avg(embedding)`을 질의 시점에 계산해 이웃을 찾았다. 그 방식을 고른 근거는 *"조회 시점 계산이라 항상 현재 청크를 따른다"*였는데, 관계 edge를 **청크 교체와 같은 트랜잭션에서** 만드는 트리거를 채택하면서 최신성 차이가 사라졌다. 문서 대표 벡터를 컬럼으로 저장하지 않는다는 원래 판단은 그대로다 — edge는 벡터가 아니라 관계다.
>
> 따라서 이 절의 두 쿼리에는 **벡터 연산이 없고, `SET LOCAL` 두 줄도 필요 없다.** 벡터 정렬은 청크가 바뀔 때 트리거가 한 번 수행하며 그 구조는 「자동 임베딩 파이프라인」의 관계 생성 트리거에 있다.

### 세 가지 공통 규칙

**1. 권한 필터를 검색과 동일하게 적용한다**

```sql
(d.visibility = 'public' OR d.owner_id = %(user)s)
```

빠뜨리면 관련 문서가 private 문서를 노출하고, 태그 추천이 private 문서의 태그를 흘린다. 대상 문서 자체도 서비스가 `ensure_visible`로 검증한다. 현재 이 검증은 `find_related`·`suggest_tags`·`resolve_links`·`find_backlinks` 네 함수에 걸려 있어 HTTP를 거치지 않는 호출에도 같은 404 의미의 `DocumentNotFound`가 적용된다.

**2. `kind`를 섞어 `score`로 정렬하지 않는다**

```sql
ORDER BY e.kind, e.score DESC, d.id     -- kind로 묶은 뒤 그 안에서 점수순
```

`score`의 **척도가 `kind`마다 다르기** 때문이다 — `overlaps`는 매칭 비율, `related`·`points_to`는 `1.0 - 최소거리`다 (`006_edges_tables.sql`). 섞어서 정렬하면 서로 다른 단위의 숫자를 한 줄에 세우게 된다. 화면도 `kind`별로 묶어 보여준다.

> 벡터 정렬 후보를 `DISTINCT ON`으로 줄이는 순서 규칙(ADR-011 보강 1)은 이 절에 더 이상 해당하지 않는다 — 여기에는 벡터 정렬이 없다. 그 규칙이 살아 있는 곳은 **검색 쿼리**이며 「검색 데이터 흐름」 절에 있다.

**3. 청크가 없는 문서는 관계를 조회하지 않는다**

관계 edge는 청크가 만들어질 때 함께 생긴다. 청크가 0행이면 edge도 없으므로, 빈 결과를 관계 없음으로 보고하는 대신 **아직 색인 전**임을 구분해 알린다.

> ⚠️ 이 분기의 **원래 이유는 달랐다.** `avg(embedding)`이 청크 0행에서 NULL을 반환해 `embedding <=> NULL`이 정렬을 무의미하게 만들고 **에러 없이 무작위 문서 목록을 반환**하는 것을 막는 방어였다. 지금 이 절에는 `avg`가 없지만, **규칙은 재도입 대비로 `CLAUDE.md`에 남아 있다.** 벡터 정렬을 이 경로에 다시 넣는다면 그 함정이 함께 돌아온다.

```
GET /api/documents/{id}/related
GET /api/documents/{id}/tag-suggestions

  /related, 청크 0건        → 200 { "items": [], "identical": [...], "based_on_version": null, "reason": "not_indexed" }
  /related, 청크 O·edge 0건 → 200 { "items": [], "identical": [...], "based_on_version": 2,    "reason": "no_edges" }
  /related, 청크 O·edge O   → 200 { "items": [...], "identical": [...], "based_on_version": 2, "reason": null }
  /tag-suggestions, 청크 0건 → 200 { "items": [], "based_on_version": null, "reason": "not_indexed" }
  /tag-suggestions, 청크 있음 → 200 { "items": [...], "based_on_version": 2, "reason": null }
```

- **`no_edges`는 `not_indexed`와 다르다.** 색인은 끝났는데 관련성이 옅어 저장된 관계가 하나도 없는 상태다. edge 방식은 이 경우가 실제로 생기므로 *"관련 문서 없음"*을 정직하게 표시한다 (ADR-029 결정 5). 질의 시점 벡터 계산에서는 늘 최근접 k개가 나와 이 구분이 없었다

- **404·400이 아니라 200이다.** 문서는 존재하고 요청도 유효하다. "아직 색인 전"은 오류가 아니라 상태다
- 분기 기준은 `embedding_status`가 **아니라 청크 존재 여부**다. 재임베딩 중(`processing`)에도 이전 청크가 남아 있으므로 정상 응답해야 하며, 이는 검색이 재임베딩 중 이전 벡터로 동작하는 정책과 일치한다
- `based_on_version`은 `document_chunks.version`을 그대로 쓴다. UI에 "v2 기준"으로 표시되어 **버전 일관성 보장을 화면에서 뒷받침한다**
- 발생 상황: 최초 업로드 직후(`pending`), 최초 임베딩 실패(`error`)
- `/related`의 `identical`은 벡터가 아니라 `content_hash`로 계산하므로, 청크가 없는 `not_indexed` 상태에서도 반환된다

### 관련 문서

```sql
BEGIN;  -- plain BEGIN (ADR-010). 벡터 연산이 없어 SET LOCAL 두 줄은 걸지 않는다

-- 1) 청크 상태 — not_indexed 분기와 based_on_version을 함께 얻는다
SELECT count(*), min(version) FROM document_chunks WHERE document_id = %(id)s;

-- 2) 동일 텍스트 문서 (벡터가 아니라 content_hash. 청크가 없어도 반환한다)
SELECT d.id, d.title
FROM documents me
JOIN documents d ON d.content_hash = me.content_hash AND d.id <> me.id
WHERE me.id = %(id)s
  AND (d.visibility = 'public' OR d.owner_id = %(user)s)
ORDER BY d.created_at, d.id;

-- 3) 저장된 관계 — 벡터 정렬 없이 edge를 읽기만 한다
SELECT d.id, d.title, d.tags, e.kind, e.score
FROM document_edges e
JOIN documents d ON d.id = e.dst_document_id
WHERE e.src_document_id = %(id)s
  AND (d.visibility = 'public' OR d.owner_id = %(user)s)   -- ★ 열람 범위는 조회 시점에
ORDER BY e.kind, e.score DESC, d.id
LIMIT %(k)s;

COMMIT;
```

> **권한은 저장이 아니라 조회에서 건다.** 워커에는 사용자 컨텍스트가 없으므로 edge 자체는 권한과 무관하게 만들어진다. 열람 범위는 **읽을 때** 적용하며, 비공개 문서로 향하는 edge는 그 사용자에게 없는 것처럼 보인다 (ADR-027).

**`score`가 무엇인지 정확히** — `kind`마다 척도가 다르다. 하나의 유사도 축이 아니다.

| `kind` | `score`의 의미 | 계산 |
|---|---|---|
| `overlaps` | 자기 대목 중 상대 문서에서 최근접 이웃을 찾은 **비율** | `overlap_ratio` |
| `related` · `points_to` | 가장 가까운 청크 쌍의 **유사도** | `1.0 - 최소거리` |

`008_edges_triggers.sql:119`의 `CASE WHEN is_overlaps THEN overlap_ratio ELSE 1.0 - min_dist END`가 그 자리다. **비율과 거리는 같은 줄에 세울 수 없으므로 `kind`를 섞어 정렬하지 않는다**(공통 규칙 2).

> **`overlaps`를 "같은 내용"으로 읽으면 안 된다.** 이웃 판정이 순위 기반이라 절대 거리 임계가 없고, 주제가 가까운 문서끼리는 모든 대목이 서로 최근접이 되어 비율이 1.0에 붙는다 — 실 BGE-M3 실측에서 `PRD`↔`UI 디자인 가이드`가 1.00이었다 (`OPENSQL_RESEARCH.md` §14). 화면 어휘를 「여러 대목에서 만난다」로 두고 대목 수만 달리 말하는 이유다 (`UI_GUIDE.md`).

### 유사 후보 표시 — 중복 "탐지"가 아니다

위 `score`는 **중복 판정에 쓰지 않는다.** 완전히 같은 문서도 주제가 여럿이면 점수가 낮게 나올 수 있고(거짓 음성), 긴 문서가 짧은 문서를 포함하면 높게 나온다(포함이지 중복이 아님, 거짓 양성).

**저장된 `score`는 양방향이 같지만, 그것은 근사다.** 트리거가 `both_directions`로 같은 값을 A→B와 B→A에 함께 넣는다(`008_edges_triggers.sql:122`). kNN 자체는 대칭이 아닌데, 정확한 역방향을 구하려면 기존 문서 전부를 다시 계산해야 해 비용이 문서 수에 비례한다. 새 문서 기준 한 번의 조회를 양방향에 복사하는 쪽을 택했다 — 중복 판정처럼 정밀도가 필요한 곳에 쓸 수 없는 또 하나의 이유다.

| 신호 | 판정 | 방법 |
|---|---|---|
| `content_hash` 완전 일치 | **동일 텍스트** — 확정 | `SELECT id, title FROM documents WHERE content_hash = %(hash)s AND id <> %(id)s` |
| `score` 상위 항목 | **내용이 유사한 문서** — 참고용 | 위 관련 문서 쿼리 |

자동 차단·병합·업로드 거부를 붙이지 않고, 고정 임계값으로 "중복" 배지를 켜지도 않는다. UI 문구는 **"내용이 유사한 문서가 있습니다"**이며 판단은 사람이 한다.

### 태그 추천

태그를 임베딩하지 않는다. 유사 문서를 찾은 뒤 **그 문서들의 태그를 빈도순**으로 제시한다 (ADR-019).

```sql
BEGIN;  -- plain BEGIN (ADR-010). 관련 문서와 같은 이유로 SET LOCAL이 없다

WITH neighbors AS (            -- 저장된 관계에서 이웃 10건 (NEIGHBOR_LIMIT)
  SELECT e.dst_document_id AS document_id
  FROM document_edges e
  JOIN documents d ON d.id = e.dst_document_id
  WHERE e.src_document_id = %(id)s
    AND (d.visibility = 'public' OR d.owner_id = %(user)s)
  ORDER BY e.kind, e.score DESC, d.id      -- 관련 문서와 같은 정렬 (공통 규칙 2)
  LIMIT 10
)
SELECT t.tag, count(*) AS freq
FROM neighbors n
JOIN documents d ON d.id = n.document_id
CROSS JOIN LATERAL unnest(d.tags) AS t(tag)
WHERE NOT (t.tag = ANY(%(current_tags)s::text[]))   -- 이미 달린 태그 제외
GROUP BY t.tag ORDER BY freq DESC, t.tag LIMIT %(limit)s;

COMMIT;
```

`documents.tags`(정형 배열)와 저장된 관계가 한 쿼리에서 결합된다 — 하이브리드 활용 사례가 하나 더 늘어난다. 문서가 적을 때는 이웃이 없어 추천도 비는데, 이는 콜드스타트로 수용한다.

**이웃 수(10)와 추천 개수(`limit`, 기본 5)는 다르다.** 이웃 10건에서 태그를 모아 빈도순으로 정렬한 뒤 `limit`개를 자른다. 이웃을 추천 개수로 자르면 빈도를 셀 표본이 사라진다.

## 임베딩 프로바이더

```python
class EmbeddingProvider(Protocol):
    name: str
    dimension: int  # 항상 1024
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

- **`LocalProvider`**: sentence-transformers `BAAI/bge-m3` (MIT, 1024차원, 8192 토큰, 한국어 강점). 워커에서 lazy-load. **운영 경로는 이것 하나뿐이다.**
- **`FakeProvider`**: 결정론적 해시 기반 벡터 — 테스트 전용. 모델 로딩 없이 파이프라인 전체를 CI 속도로 테스트하는 TDD의 핵심 장치.
- 선택은 `EMBEDDING_PROVIDER` 환경변수 (`local` | `fake`). 질의 임베딩도 동일 프로바이더를 쓴다 — 질의-문서 벡터 공간이 일치해야 한다.
- **상용 API 기반 프로바이더는 구현하지 않는다** (ADR-003). 대회 규정 [별표2]가 "외부 API 호출을 통해서만 작동하는 API 전용 모델 사용 불가"를 명시한다.
- 배칭·캐싱·폴백 체인은 만들지 않는다.

### 청킹 (services/chunking.py)

순수 함수. 문단 경계 우선 분할, 청크 최대 1,000자, 인접 청크 150자 오버랩. 외부 의존성 없이 단위 테스트 가능해야 한다.

## 프론트엔드 패턴

- 사용자 화면: `/`(목록 + 업로드 드롭존), `/documents/[id]`(메타데이터·텍스트 버전 이력·청크 수와 기준 버전 요약·**문서 텍스트 편집**·관련 문서·태그 추천), `/search`(질의 + 태그/유형 필터 + 결과. "실행된 SQL 보기" 토글), `/clusters`(태그 덩어리와 연결), `/diagnostics`(고아·중복 후보·미분류·깨진 링크), `/login`
- **편집은 Client Component**다. 보기 ↔ 편집 토글, 저장 시 `version`을 함께 전송하고 409를 처리한다. 저장 직후 상태 배지가 `pending → processing → ready`로 바뀌는 것을 2초 폴링으로 보여준다
- **사용자 화면은 인프라 상태를 노출하지 않는다.** 페일오버가 나도 화면 구성이 달라지지 않으며, 사용자는 업로드·검색이 계속 성공하는 것만 본다 (UI_GUIDE 디자인 원칙 3).
- 관리 화면: `/admin/status` — `GET /api/system/status`를 폴링해 접속 노드·잡 수·프로바이더 표시. **페일오버 데모의 증거 채널**이며 사용자 내비게이션에 노출하지 않는다. `/admin/users` — 계정 발급·삭제 (ADR-028)
- 화면은 모두 Client Component다. 로그인 세션 확인과 목록·상세·관리 화면의 폴링 때문이다
- API 연동은 `next.config.js` rewrites로 FastAPI 프록시

## 상태 관리

- 서버 상태: fetch 기반. 목록과 `/admin/status`는 상시 2초 폴링하고, 문서 상세는 `pending`·`processing`일 때만 2초 폴링하며 `ready`·`error`에서는 멈춘다. 폴링 실패 시 마지막 성공 데이터를 유지한다. SSE/웹소켓 사용 안 함
- 클라이언트 상태: useState만 사용하고 전역 상태 라이브러리는 두지 않는다. 로그인 사용자는 서버 세션 쿠키가 정하며(ADR-028), 화면은 `GET /api/auth/me`로 확인만 한다
