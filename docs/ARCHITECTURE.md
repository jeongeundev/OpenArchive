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
                  │  document_chunks (vector(1024), HNSW)         │
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

핵심 프레이밍: **잡 생성·코얼레싱·삭제 정합성은 전부 DB 안**(트리거 함수, 파셜 유니크 인덱스, FK CASCADE)에서 보장된다. 워커는 "DB가 만들어 둔 잡을 집어가는 무상태 실행기"이며, DB 밖 연산은 임베딩 모델 추론뿐이다.

> **기동(전달) 방식은 정합성의 일부가 아니다.** 워커가 잡을 언제 집어가든 — NOTIFY로 즉시든 폴링으로 5초 뒤든 — 잡이 유실되거나 중복 처리되지 않는 것은 아웃박스 테이블과 `SKIP LOCKED`가 보장한다. 그래서 `LISTEN`/`NOTIFY`가 OpenProxy를 통과하지 못해도 이 설계의 핵심 주장은 무너지지 않는다 (ADR-009).

## 디렉토리 구조

```
OpenArchive/
├── docker-compose.yml            # 로컬 개발용 pgvector 컨테이너
├── scripts/check.sh              # 통합 검증 (backend lint+test, frontend lint+test+build)
├── backend/
│   ├── pyproject.toml            # fastapi, psycopg[binary,pool], pydantic-settings, pypdf, python-docx / [dev]: pytest, ruff / [local]: sentence-transformers
│   ├── migrations/               # 001_extensions.sql, 002_tables.sql, 003_triggers.sql, 004_indexes.sql
│   ├── app/
│   │   ├── main.py               # FastAPI 앱 조립
│   │   ├── config.py             # pydantic-settings (DATABASE_URL, EMBEDDING_PROVIDER 등)
│   │   ├── db.py                 # AsyncConnectionPool만 — import 시 부작용 없음
│   │   ├── migrations.py         # 마이그레이션 러너 — API 서버 startup에서만 호출
│   │   ├── api/                  # 라우터: documents.py, search.py, system.py
│   │   ├── services/             # parsing.py, chunking.py, documents.py, search.py
│   │   ├── embeddings/           # base.py(Protocol), local.py(bge-m3), fake.py
│   │   └── worker.py             # 임베딩 워커 진입점
│   ├── mcp_server/server.py      # FastMCP stdio 서버 — app.services를 직접 import
│   └── tests/                    # test_chunking.py, test_triggers.py, test_worker.py, test_search_api.py ...
└── frontend/
    └── src/
        ├── app/                  # /(목록+업로드), /documents/[id], /search, /admin/status
        ├── components/
        ├── types/
        └── lib/                  # API 클라이언트 (fetch 래퍼)
```

## DB 스키마

```sql
-- documents: 정형 메타데이터 + 버전 + 권한 (하이브리드 활용의 절반)
CREATE TABLE documents (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title            text NOT NULL,
  filename         text,                   -- 업로드된 원본 파일명 (출처 표시용). 파일 자체는 보관하지 않는다
  content_type     text NOT NULL,          -- pdf | docx | txt | md
  content          text NOT NULL,          -- 추출 텍스트 (현재 버전). 편집·버전 관리·임베딩의 대상
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

-- document_versions: 추출 텍스트의 버전 이력 (append-only)
-- 파일 버전 이력이 아니다. v1으로 되돌려도 원본 파일이 아니라 v1 시점의 추출 텍스트가 나온다.
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
```

설계 근거: 잡은 콘텐츠 페이로드 없이 "이 문서는 재임베딩이 필요하다"는 신호만 담는다. 워커가 처리 시점에 `documents`의 최신 content를 읽으므로 (a) 연속 수정이 자연스럽게 코얼레싱되고 (b) 재처리가 최신 상태로 수렴하는 멱등 구조가 된다.

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
- pgvector 0.8+로 확인되면 `SET LOCAL hnsw.iterative_scan = relaxed_order`를 추가로 적용할 수 있다. **버전 미확인이므로 조건부다** — `docs/OPENSQL_RESEARCH.md` §8.

> ⚠️ **HNSW 인덱스 자체의 사용 가능 여부가 미확인이다.** OpenSQL 문서에 pgvector 버전과 HNSW 지원 여부가 명시되어 있지 않다. M0에서 `CREATE INDEX ... USING hnsw`를 실제로 실행해 확인하고, 불가하면 pgvectorscale 또는 IVFFlat으로 전환한다 (ADR-002).

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
  -- content_hash <> 읽었던 값이면 → ROLLBACK, job은 done 처리
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

5. 좀비 회수: `processing` 상태로 5분 초과된 잡을 워커 기동 시 + 주기 스윕에서 `pending`으로 리셋. `attempts`는 초기화하지 않는다 — 초기화하면 계속 죽는 잡이 영원히 재시도되어 재시도 상한이 무의미해진다.

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

> **이 표가 보장하지 않는 것: "항상 최신".** 재임베딩 중에는 이전 버전이 검색되고, 폴링 주기(5초)와 임베딩 소요만큼 반영이 늦으며, Failover 구간에는 요청이 실패한다. 우리가 보장하는 것은 **버전 일관성**과 **최신으로의 수렴**이며, 그 사이의 어긋난 구간은 정합성 검증 쿼리로 **관측할 수 있다**. 문서·데모에서 "항상 최신"이나 "실시간 동기화"로 표현하지 않는다 (ADR-015).

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
| 클러스터 상태 공유 | **OpenHA DCS (etcd)** |
| Primary 변경 감지 후 재연결, 커넥션 풀링, VIP 이중화 | **OpenProxy** |
| 미처리 잡의 무손실 보존 | **DB 계층** (`embedding_jobs`는 WAL 로깅 테이블 → 스탠바이 복제) |
| 연결 끊김 시 재시도, 잡 재개, 좀비 회수 | **애플리케이션 (우리)** |

> **`PROJECT_CONTEXT.md` 설계 원칙 준수**: 위 표의 상위 3줄은 OpenSQL이 제공하는 기능이므로 애플리케이션에서 중복 구현하지 않는다 (ADR-006).

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

### Failover 시간 특성

OpenSQL이 배포하는 `patroni.yml` 기준값:

| 파라미터 | 값 |
|---|---|
| `ttl` | 30초 |
| `loop_wait` | 10초 |
| `retry_timeout` | 10초 |
| `maximum_lag_on_failover` | 1MB |
| `failsafe_mode` | `true` |

> **"무중단"이 아니라 "짧은 중단 후 자동 복구"다.** 장애 감지부터 승격까지 수십 초가 걸린다. 사용자 관점에서는 그 구간의 요청이 실패하고 이후 정상 복구된다. 문서·데모에서 이 표현을 정확히 쓴다.

### 제약: `max_connections = 100`

OpenSQL `patroni.yml`의 PostgreSQL 파라미터는 `max_connections: 100`이다. API 풀 + 워커 잡 처리 풀 + 워커 LISTEN 연결이 모두 이 안에 들어가야 하며, OpenProxy의 `pool_size`와 함께 산정한다.

### 로컬 개발 갭

로컬은 pgvector 단일 컨테이너다 (ADR-007). **OpenProxy 경유 경로는 로컬에서 검증할 수 없다** — 공식 Docker 배포판이 없기 때문이다. M0 검증 목록은 `docs/OPENSQL_RESEARCH.md` §12를 따른다.

## API 설계

| 엔드포인트 | 내용 |
|---|---|
| `POST /api/documents` | multipart 업로드. pypdf/python-docx/plain 파싱 → INSERT. 여기서 트리거가 파이프라인을 자동 기동 — 임베딩 관련 코드 없음. **텍스트 추출 결과가 비면 400** (아래). **원본 파일은 보관하지 않는다** — 추출 텍스트만 저장하고 파일은 버린다 |
| `GET /api/documents` | 목록 + `status`/`tag` 필터, embedding_status 포함 |
| `GET /api/documents/{id}` | 상세 + 텍스트 버전 목록 + 청크 수 + 청크 기준 버전 |
| `PUT /api/documents/{id}` | 새 파일 또는 편집된 추출 텍스트 → `version`+1, `content`, `content_hash` UPDATE. **버전 이력 기록과 재임베딩 잡 생성은 트리거가 수행.** 요청의 `version`이 현재 버전과 다르면 **409** (아래) |
| `DELETE /api/documents/{id}` | CASCADE로 벡터까지 원자 삭제 |
| `POST /api/documents/{id}/reembed` | **임베딩 실패 복구.** 아래 참조 |
| `GET /api/documents/{id}/related` | **관련 문서.** 청크 평균 벡터로 유사 문서 조회 (ADR-018). 청크가 없으면 `not_indexed` |
| `GET /api/documents/{id}/tag-suggestions` | **태그 추천.** 유사 문서의 태그 빈도 (ADR-019). 청크가 없으면 `not_indexed` |
| `POST /api/search` | 하이브리드 검색 (아래) |
| `GET /api/system/status` | **운영/데모 전용**: `inet_server_addr()`(현재 접속 노드), pending/processing/error 잡 수, 임베딩 프로바이더명, 최근 재연결 이벤트, **정합성 검증 쿼리 결과**(`c.version <> d.version` 건수). `/admin/status`가 소비하며 사용자 화면은 호출하지 않는다 |

> **구현 현황 (M2 기준)**: `/related`·`/tag-suggestions`를 뺀 나머지가 구현되어 있다. 두 엔드포인트와 `reconnect_events` 값 채우기는 각각 M4·M5 몫이다.
>
> `PUT`은 현재 **편집된 추출 텍스트만** 받는다(`{content, version}` JSON). 표에 적힌 "새 파일" 경로는 아직 없다 — 새 파일을 올리려면 업로드 후 이전 문서를 삭제해야 한다.
>
> 라우터는 얇다. 요청 검증과 상태 코드 변환만 하고 실제 로직은 `services/documents.py`·`services/search.py`에 있으며, MCP 서버가 같은 함수를 재사용한다. 도메인 예외를 상태 코드로 옮기는 매핑은 `main.py`의 exception handler 한 곳에 있다.

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

플랫폼 안에서 문서를 고칠 수 있다. **편집 대상은 추출 텍스트이며 원본 파일이 아니다** (ADR-017).

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

**원본 파일과 추출 텍스트를 구분한다.** 현재 스키마에 바이너리 컬럼이 없으므로, 편집 후에는 `filename = report.pdf`인데 `content`가 그 PDF의 추출 결과와 다른 상태가 될 수 있다. **결함이 아니라 설계된 동작**이며, 스캔 품질이 나쁜 PDF의 오추출을 고치는 정당한 용도가 있다. UI는 편집 영역을 "본문"이 아니라 **"추출 텍스트"**로 표기한다 (`UI_GUIDE.md`).

## 검색 데이터 흐름

질의 텍스트 → 동일 프로바이더로 질의 임베딩 → 단일 하이브리드 SQL:

```sql
BEGIN;  -- ★ plain BEGIN. READ ONLY 금지 (아래 설명)

SET LOCAL hnsw.ef_search = 200;      -- 필터 통과 후보를 충분히 확보 (기본 40)
SET LOCAL random_page_cost = 1.1;    -- 무필터 검색이 HNSW를 타게 한다 (ADR-011 보강 5)

WITH candidates AS (
    -- 1단계: HNSW 인덱스로 후보를 넉넉히 뽑는다 (k의 5배)
    SELECT c.document_id, c.chunk_index, c.content,
           c.embedding <=> %(qvec)s AS dist
    FROM document_chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)
      AND (%(ctype)s::text IS NULL OR d.content_type = %(ctype)s)
      AND (d.visibility = 'public' OR d.owner_id = %(user)s)
    ORDER BY c.embedding <=> %(qvec)s
    LIMIT %(k)s * 5
),
best_per_doc AS (
    -- 2단계: 문서당 최고 점수 청크 1개만 남긴다
    SELECT DISTINCT ON (document_id) *
    FROM candidates
    ORDER BY document_id, dist
)
SELECT d.id, d.title, d.tags, d.content_type,
       b.chunk_index, b.content, 1 - b.dist AS score
FROM best_per_doc b
JOIN documents d ON d.id = b.document_id
ORDER BY b.dist
LIMIT %(k)s;

COMMIT;
```

정형 필터·권한 술어·벡터 정렬이 한 쿼리에 결합된다(가산점 포인트). 이 쿼리는 `services/search.py` 한 곳에만 존재하며 REST API와 MCP 서버가 공유한다.

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

### 조건부 확장: 키워드 + 벡터 RRF (ADR-016)

> **이 절은 선택 사항이다.** core 요건("정형 필터 + 벡터를 단일 SQL로 결합")은 위 쿼리로 충족된다. 아래는 core가 완성된 뒤 일정에 여유가 있을 때만 착수하며, 착수하지 않아도 나머지 설계는 그대로 성립한다.

착수 시 스키마와 인덱스를 하나씩 추가한다. `content_tsv`는 **`GENERATED ALWAYS AS ... STORED`이므로 애플리케이션 코드 없이 DB가 유지한다** — 청크가 쓰이는 순간 키워드 색인이 함께 생긴다.

```sql
-- migrations/005_fts.sql
ALTER TABLE document_chunks
  ADD COLUMN content_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED;

CREATE INDEX idx_chunks_tsv ON document_chunks USING gin (content_tsv);
```

검색 쿼리는 위 구조를 유지한 채 CTE 두 개를 추가하고, `DISTINCT ON`을 **RRF 융합 이후**로 옮긴다 (ADR-011 보강 2).

```sql
BEGIN;
SET LOCAL hnsw.ef_search = 200;
SET LOCAL random_page_cost = 1.1;    -- 검색 쿼리와 동일한 이유 (ADR-011 보강 5)

WITH vec AS (          -- 위 candidates 절과 동일한 필터·JOIN 구조
  SELECT c.id, c.document_id, c.chunk_index, c.content,
         row_number() OVER (ORDER BY c.embedding <=> %(qvec)s) AS rnk
  FROM document_chunks c JOIN documents d ON d.id = c.document_id
  WHERE (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)
    AND (%(ctype)s::text IS NULL OR d.content_type = %(ctype)s)
    AND (d.visibility = 'public' OR d.owner_id = %(user)s)
  ORDER BY c.embedding <=> %(qvec)s LIMIT %(k)s * 5
),
kw AS (                -- GIN 인덱스 경로. 필터는 vec과 완전히 동일해야 한다
  SELECT c.id, c.document_id, c.chunk_index, c.content,
         row_number() OVER (ORDER BY ts_rank(c.content_tsv, q) DESC) AS rnk
  FROM document_chunks c JOIN documents d ON d.id = c.document_id,
       websearch_to_tsquery('simple', %(query)s) q
  WHERE c.content_tsv @@ q
    AND (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)
    AND (%(ctype)s::text IS NULL OR d.content_type = %(ctype)s)
    AND (d.visibility = 'public' OR d.owner_id = %(user)s)
  ORDER BY ts_rank(c.content_tsv, q) DESC LIMIT %(k)s * 5
),
fused AS (             -- RRF: 1/(60 + rank)
  SELECT COALESCE(v.id, w.id) AS id,
         COALESCE(v.document_id, w.document_id) AS document_id,
         COALESCE(v.chunk_index, w.chunk_index) AS chunk_index,
         COALESCE(v.content, w.content) AS content,
         COALESCE(1.0/(60 + v.rnk), 0) + COALESCE(1.0/(60 + w.rnk), 0) AS score
  FROM vec v FULL OUTER JOIN kw w ON v.id = w.id
),
best_per_doc AS (      -- 문서당 1건 — RRF 이후에 적용한다
  SELECT DISTINCT ON (document_id) * FROM fused
  ORDER BY document_id, score DESC
)
SELECT d.id, d.title, d.tags, d.content_type, b.chunk_index, b.content, b.score
FROM best_per_doc b JOIN documents d ON d.id = b.document_id
ORDER BY b.score DESC LIMIT %(k)s;

COMMIT;
```

> ⚠️ **한국어에서 기대 효과가 제한적이다.** 번들 확장에 한국어 형태소 분석기가 없어 `simple` 파서를 쓰는데, 이는 조사를 분리하지 못한다 — `"OpenSQL의"`는 `opensql의`로 색인되어 `opensql`로 검색되지 않는다. 효과는 **조사 없이 등장하는 토큰**(독립 표기된 제품명·영문 약어·숫자·에러 코드)에 한정되며, 실제 비중은 실측 전까지 알 수 없다. ADR-016을 조건부로 둔 이유다.

## 관련 문서·태그 추천

문서 상세에서 두 가지를 제공한다. 둘 다 대상 문서의 **청크 평균 벡터를 질의 시점에 계산**하며, 문서 대표 벡터를 컬럼으로 저장하지 않는다 (ADR-018).

### 세 가지 공통 규칙

**1. 권한 필터를 검색과 동일하게 적용한다**

```sql
(d.visibility = 'public' OR d.owner_id = %(user)s)
```

빠뜨리면 관련 문서가 private 문서를 노출하고, 태그 추천이 private 문서의 태그를 흘린다. 대상 문서 자체의 접근 권한은 API 레이어의 문서 조회에서 이미 걸린다.

**2. `DISTINCT ON`은 후보 확보 이후에 (ADR-011 보강 1)**

```
1) 벡터 정렬 + LIMIT 으로 후보 확보   ← 여기서만 인덱스를 탄다
2) DISTINCT ON (document_id) 로 문서당 1건
3) 거리순 재정렬 + 최종 LIMIT
```

2)와 3)을 합쳐 `DISTINCT ON ... ORDER BY document_id, dist LIMIT k`로 쓰면 **유사도가 아니라 `document_id`(UUID) 순으로 잘린다.**

**3. 청크가 없는 문서는 쿼리를 실행하지 않는다**

`avg(embedding)`은 청크가 0행이면 **NULL을 반환**한다. 그러면 `embedding <=> NULL`이 NULL이 되어 정렬이 무의미해지고, **에러 없이 무작위 문서 목록이 반환된다.** 애플리케이션이 먼저 막는다.

```
GET /api/documents/{id}/related
GET /api/documents/{id}/tag-suggestions

  청크 0건 → 200 { "items": [], "reason": "not_indexed" }
  청크 있음 → 200 { "items": [...], "based_on_version": 2 }
```

- **404·400이 아니라 200이다.** 문서는 존재하고 요청도 유효하다. "아직 색인 전"은 오류가 아니라 상태다
- 분기 기준은 `embedding_status`가 **아니라 청크 존재 여부**다. 재임베딩 중(`processing`)에도 이전 청크가 남아 있으므로 정상 응답해야 하며, 이는 검색이 재임베딩 중 이전 벡터로 동작하는 정책과 일치한다
- `based_on_version`은 `document_chunks.version`을 그대로 쓴다. UI에 "v2 기준"으로 표시되어 **버전 일관성 보장을 화면에서 뒷받침한다**
- 발생 상황: 최초 업로드 직후(`pending`), 최초 임베딩 실패(`error`)

### 관련 문서

```sql
BEGIN;  -- ★ SET LOCAL은 트랜잭션 밖에서 경고만 내고 무효다. plain BEGIN (ADR-010)
SET LOCAL hnsw.ef_search = 200;      -- k*10 보다 커야 한다 (ADR-011 보강 4)
SET LOCAL random_page_cost = 1.1;    -- 없으면 플래너가 HNSW를 버린다 (ADR-011 보강 5)

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
ORDER BY b.dist LIMIT %(k)s;

COMMIT;
```

> **왜 필터가 `cand` 안에 있나 — 한 번 밖으로 뺐다가 되돌렸다 (2026-08-05).**
>
> 1차 실측은 "`cand` 안에 JOIN이 있으면 `enable_seqscan=off`로도 HNSW를 못 쓴다(255ms vs 106ms)"로
> 읽고 필터를 밖으로 뺐다. **재측정에서 재현되지 않았다** — 위 형태 그대로 HNSW를 정상 사용한다
> (강제 하 32.7ms, 무강제 `rpc=1.1`에서 33.8ms). 플래너는 벡터 정렬을 인덱스로 처리하고
> `documents`를 그 뒤에 nested loop로 붙인다 (`OPENSQL_RESEARCH.md` §12 17번, ADR-018 재개정).
>
> **필터를 밖으로 빼면 대가만 남는다.** 비공개 문서의 청크가 후보 자리를 차지하고 버려져,
> 후보 100개가 퍼지는 문서 수가 **54.5개 → 40.5개로 줄었다**(비공개 20%, 문서 20건 표본).
> `k=10`에는 둘 다 여유가 있으나 얻는 것이 없는 손실이다.
>
> **진짜 변수는 `random_page_cost`였다.** 기본값 4에서는 어떤 형태도 HNSW를 쓰지 않는다
> (624~785ms). 1.1로 낮추면 쿼리를 그대로 두고 33~36ms가 된다 (ADR-011 보강 5).

**`score`가 무엇인지 정확히** — 문서 간 유사도가 아니다.

```
score = 1 − distance( 대상 문서의 청크 평균 벡터 ,  후보 문서에서 가장 가까운 단일 청크 )
                     └── 문서 전체를 뭉친 값 ──┘   └──── 최댓값 하나만 ────┘
```

대상 쪽은 **평균**, 후보 쪽은 **최댓값**이라 **비대칭**이다. 의미는 *"이 문서 전반의 주제에 가장 잘 맞는 구절을 가진 문서"*이며 A→B와 B→A 점수가 다를 수 있다. 필드명을 `sim`이 아니라 `score`로 둔 이유다.

### 유사 후보 표시 — 중복 "탐지"가 아니다

위 `score`는 **중복 판정에 쓰지 않는다.** 비대칭이라 양방향으로 실패한다 — 완전히 같은 문서도 주제가 여럿이면 점수가 낮게 나올 수 있고(거짓 음성), 긴 문서가 짧은 문서를 포함하면 높게 나온다(포함이지 중복이 아님, 거짓 양성).

| 신호 | 판정 | 방법 |
|---|---|---|
| `content_hash` 완전 일치 | **동일 텍스트** — 확정 | `SELECT id, title FROM documents WHERE content_hash = %(hash)s AND id <> %(id)s` |
| `score` 상위 항목 | **내용이 유사한 문서** — 참고용 | 위 관련 문서 쿼리 |

자동 차단·병합·업로드 거부를 붙이지 않고, 고정 임계값으로 "중복" 배지를 켜지도 않는다. UI 문구는 **"내용이 유사한 문서가 있습니다"**이며 판단은 사람이 한다.

### 태그 추천

태그를 임베딩하지 않는다. 유사 문서를 찾은 뒤 **그 문서들의 태그를 빈도순**으로 제시한다 (ADR-019).

```sql
BEGIN;  -- ★ 관련 문서와 같은 이유 — SET LOCAL은 트랜잭션 안에서만 유효하다
SET LOCAL hnsw.ef_search = 200;      -- 아래 LIMIT 100 보다 커야 한다 (ADR-011 보강 4)
SET LOCAL random_page_cost = 1.1;    -- 관련 문서와 같은 이유 (ADR-011 보강 5)

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
GROUP BY t.tag ORDER BY freq DESC, t.tag LIMIT 5;

COMMIT;
```

`documents.tags`(정형 배열)와 벡터 이웃이 한 쿼리에서 결합된다 — 하이브리드 활용 사례가 하나 더 늘어난다. 문서가 적을 때는 이웃이 없어 추천도 비는데, 이는 콜드스타트로 수용한다.

> **`avg`는 인덱스를 탄다 — 실측 완료 (2026-08-05).** 플래너가 `(SELECT v FROM me)`를 `InitPlan`으로
> 접어 한 번만 평가하고 HNSW 프로브로 쓴다. 대비하던 "왕복 2회" 대안은 필요 없다.
> **`cand` 안의 JOIN도 인덱스를 막지 않는다** — 1차 실측의 그 결론은 재측정에서 재현되지 않았다.
> `docs/OPENSQL_RESEARCH.md` §12 12·17번, ADR-018 재개정.

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

- 사용자 3화면: `/`(목록 + 업로드 드롭존), `/documents/[id]`(메타데이터·텍스트 버전 이력·청크·**추출 텍스트 편집**·재업로드·관련 문서·태그 추천), `/search`(질의 + 태그/유형 필터 + 결과. "실행된 SQL 보기" 토글)
- **편집은 Client Component**다. 보기 ↔ 편집 토글, 저장 시 `version`을 함께 전송하고 409를 처리한다. 저장 직후 상태 배지가 `pending → processing → ready`로 바뀌는 것을 2초 폴링으로 보여준다
- **사용자 화면은 인프라 상태를 노출하지 않는다.** 페일오버가 나도 화면 구성이 달라지지 않으며, 사용자는 업로드·검색이 계속 성공하는 것만 본다 (UI_GUIDE 디자인 원칙 3).
- 운영 1화면: `/admin/status` — `GET /api/system/status`를 폴링해 접속 노드·잡 수·프로바이더 표시. **페일오버 데모의 증거 채널**이며 사용자 내비게이션에 노출하지 않는다.
- Server Components 기본, 폴링·업로드 등 인터랙션 구간만 Client Component
- API 연동은 `next.config.js` rewrites로 FastAPI 프록시

## 상태 관리

- 서버 상태: fetch + 2초 폴링 (임베딩 상태 배지, 시스템 상태바). SSE/웹소켓 사용 안 함
- 클라이언트 상태: useState/useReducer만. 전역 상태 라이브러리 없음
