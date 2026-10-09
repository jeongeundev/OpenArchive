# 아키텍처

## 시스템 개요

```
┌─────────────┐   ┌──────────────────────────────────────────────┐
│  Next.js UI  │──▶│  FastAPI (backend/openarchive)               │
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
                  │  ready 전이 ──AFTER trigger──▶ 관계 잡(edges)   │
                  │           └──워커가 별도 트랜잭션에서 판정──▶   │
                  │              document_edges (저장된 관계)      │
                  │  pg_notify('embedding_jobs') — 커밋 시 발행    │
                  └───────────────┬──────────────────────────────┘
                                  │ 5초 폴링 (주 경로)
                                  │ + LISTEN/NOTIFY (최적화, 선택)
                  ┌───────────────▼──────────────────────────────┐
                  │  Worker (python -m openarchive.worker)        │
                  │  SKIP LOCKED claim → 청킹 → 임베딩 →           │
                  │  해시 재확인 + 청크 교체 + job done (단일 트랜잭션)│
                  │  관계 잡(edges)은 판정만 — 자기 트랜잭션       │
                  └──────────────────────────────────────────────┘
```

> **다섯 구분으로 읽기 (설명용 개념 모델, ADR-031)**: 위 다이어그램에서 Next.js UI·MCP
> Server·REST API가 **Interface**(같은 services를 소비하는 대등한 인터페이스), 업로드
> API에서 `create_document`까지가 **Ingestion**, 트리거와 Embedding Worker가 **Processing**,
> openSQL 클러스터가 **Storage**, 검색·관계 조회 서비스가 **Retrieval**이다. 공식 용어가
> 아니며 기존 "DB 계층"·커밋 스코프 어휘를 대체하지 않는다.

**관계는 두 갈래로 만들어지고 시점이 다르다.** `document_links`는 본문이 바뀌는 즉시 트리거가 만들고(벡터 불필요), `document_edges`는 임베딩이 끝난 뒤 트리거가 기록한 **관계 잡**(`embedding_jobs.kind = 'edges'`)을 워커가 **별도 트랜잭션**에서 처리해 만든다. 둘 다 잡 생성도 판정도 **DB 계층**에 있고 애플리케이션은 읽기만 한다 — 관련 문서·태그 추천이 조회 시점 벡터 계산을 그만둔 근거다 (ADR-029 결정 3 개정·결정 5, ADR-030).

핵심 프레이밍: **잡 생성·코얼레싱·삭제 정합성은 전부 DB 안**(트리거 함수, 파셜 유니크 인덱스, FK CASCADE)에서 보장된다. 워커는 "DB가 만들어 둔 잡을 집어가는 무상태 실행기"이며, 텍스트 추출·OCR·청킹·임베딩은 DB 밖에서 실행한다. 관계 판정 규칙은 DB 함수가 소유한다.

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
│   ├── openarchive/              # 설치되는 최상위 패키지 하나 (배포 이름 openarchive-server)
│   │   ├── main.py               # FastAPI 앱 조립
│   │   ├── config.py             # pydantic-settings — $OPENARCHIVE_HOME/.env(기본 ~/.openarchive/.env)
│   │   ├── db.py                 # AsyncConnectionPool만 — import 시 부작용 없음
│   │   ├── migrations/           # __init__.py = 러너(API startup과 `openarchive init`이 호출)
│   │   │                         #   + SQL(패키지 안이라 wheel에 실린다) 001~037: extensions, tables, triggers, indexes,
│   │   │                         #   trgm, edges(006~008), auth(009), links(010~012), token(013),
│   │   │                         #   edges 재설계(014 — rebuild_document_edges), 위키링크 정규화(015),
│   │   │                         #   관계 잡 분리(016 — embedding_jobs.kind / 017 — ready 트리거,
│   │   │                         #   ADR-029 결정 3 개정), 원본 파일 판 보관(018 — document_files, ADR-046),
│   │   │                         #   문서 생성 멱등키(019 — idempotency_keys, ADR-047), 잡 lease(020 — ADR-050),
│   │   │                         #   추출 상태·추출 잡(021·022 — OCR, ADR-052), 표 셀 `\|` 위키링크(023),
│   │   │                         #   재계산 문서 잠금(024), 그룹·열람 부여(025 — ADR-044), 외부 공유(026),
│   │   │                         #   전량 재계산을 관계 잡으로(027 — enqueue_all_edge_jobs, ADR-029 결정 6 개정),
│   │   │                         #   감사 로그(028 — audit_log / 029 — 기록·거부 트리거, ADR-055),
│   │   │                         #   폴더(030 — folders·folder_grants·documents.folder_id / 031 — 폴더 감사 트리거, ADR-054),
│   │   │                         #   휴지통(032·033 — ADR-060), 토큰 만료(034 — ADR-061), 미리보기 감사(035),
│   │   │                         #   미리보기 변환본(036 — document_file_previews / 037 — 변환 잡 트리거, ADR-058)
│   │   │                         #   외부 공유·그룹·사용자 감사(041_share_principal_audit_triggers.sql, #201)
│   │   ├── cli.py                # `openarchive init`(첫 관리자 포함)·`serve`·`create-user`·`reset-password`·`rebuild-edges`·`rebuild-previews`·`reextract`
│   │   │                         #   ·`import`·`export`·`search`·`ask`·`demo` — 운영자 CLI, DB에 직접 붙는다 (ADR-039·040·046)
│   │   ├── api/                  # 라우터: documents, search, ask, system, auth, admin, groups(+principals),
│   │   │                         #   shares, admin_shares.py(관리자 공유 조회·토큰 폐기), audit(감사 로그 조회·CSV), folders(폴더), diagnostics, clusters / 미들웨어 retry (+ deps, schemas)
│   │   ├── services/             # parsing, chunking, documents, search, related,
│   │   │                         #   links, diagnostics, clusters, auth, system, visibility,
│   │   │                         #   grants(그룹·부여), shares(외부 공유), folders(폴더 트리·범위, ADR-054), audit(행위자 전달·조회, ADR-055), answer(근거 조립·답변 생성, ADR-043)
│   │   ├── answers/              # 답변 생성 프로바이더: fake·ollama (ADR-043)
│   │   ├── embeddings/           # base.py(Protocol), local.py(bge-m3), fake.py
│   │   ├── worker.py             # 워커 진입점 — 임베딩 잡과 관계 잡을 같은 큐에서 처리
│   │   ├── demo.py · demo_corpus/ # `openarchive demo` 예제 문서
│   │   ├── mcp_server/server.py  # FastMCP 도구 4개 — stdio 인스턴스·build_server
│   │   └── mcp_server/http.py    # Bearer 인증·원격 Streamable HTTP
│   └── tests/                    # test_chunking.py, test_triggers.py, test_worker.py, test_search_api.py ...
└── frontend/
    └── src/
        ├── app/                  # /(목록+업로드), /documents/[id], /search(+답변 패널), /login, /settings,
        │                         #   /diagnostics, /clusters, /admin/status, /admin/users, /admin/groups,
        │                         #   /admin/audit, /admin/shares
        ├── components/
        ├── types/
        └── lib/                  # API 클라이언트 (fetch 래퍼)
```

`services/visibility.py`의 `VISIBLE_TO_USER`는 **모든 조회 경로가 공유하는 단일 열람 술어**다. 검색·관련 문서·그래프 순회·집계·위키링크 해석이 각자 조건을 쓰면 한 곳만 빠져도 비공개 문서가 새어 나간다 (ADR-018, ADR-027). 규칙은 사용자에게 "public이거나, 소유자이거나, 본인·본인 그룹에 부여가 있다"이고, `share:<공유 uuid>` 주체에게는 "그 공유에 부여가 있다"뿐이며(ADR-044), 테이블과 주체 값 하나만 참조하는 순수 SQL이라 바인딩을 `current_setting('app.principal')`로 바꾸면 그대로 RLS 정책이 된다. 쓰기 경로의 존재 판정(`_load_for_write`)도 이 술어를 쓴다 — 보이는 사람의 쓰기는 403, 안 보이는 사람은 404.

**폴더 범위도 이 술어 안에서 판정한다** (ADR-054). 폴더에 들었고 「폴더 범위 따름」(`folder_id IS NOT NULL AND follows_folder`)인 문서는 문서 자신의 `visibility`·부여 대신 **최상위 폴더**의 범위(조직 공개 · 폴더를 만든 사람 · `folder_grants`의 사용자·그룹)로 판정하고, 「개별 지정」 문서는 지금처럼 문서 자신의 범위로 판정한다. 소유자는 어느 쪽이든 자기 문서를 본다. 최상위 폴더 판정은 문서의 폴더에서 `parent_id`를 따라 올라가는 **상관 재귀 `EXISTS (WITH RECURSIVE …)`** 조각 하나이고, 폴더 술어 `FOLDER_VISIBLE_TO_USER`(트리·폴더 목록·검색 폴더 필터)가 시작 폴더만 바꿔 같은 조각을 쓴다. 실효 범위는 저장하지 않으므로 폴더 범위·그룹 구성원 변경은 다음 조회부터 반영된다. 같은 판정을 비상관 `IN (서브쿼리)`로 쓰면 3천 청크에서 HNSW를 버리고 generic plan에서 15배 느려졌다 — 형태를 바꾸지 않는다(`db.py`의 `prepare_threshold=None`도 그 전제다). 공유 주체는 폴더를 보지 않는다(`FOLDER_VISIBLE_TO_USER`가 거짓). 볼 수 없는 폴더 안의 「개별 지정」 문서는 보이되 폴더 정보는 응답 어디에도 싣지 않는다. 소유자는 폴더 범위가 좁혀져 자기 문서의 폴더를 못 보게 될 수 있다 — 소유자에게만 상세·열람 범위 응답에 `hidden_folder: true`를 싣고(폴더 id·이름·경로는 여전히 없다) 화면이 이름 없이 알린다.

`services/grants.py`는 그룹·구성원 관리와 부여 대상 이름 해석을 맡는다(#97 b). 문서 열람 범위 조회·교체는 문서 서비스가 소유자 경계를 지키며 이 서비스를 재사용한다(ADR-044 「관리 경로」).

MCP 서버는 `openarchive.services`를 직접 재사용한다. `search_documents`는 발췌(`excerpt`)·출처(`document_id`, `title`, `filename`)·기준 버전(`based_on_version`)을 반환하고, `get_document`는 문서 텍스트와 텍스트 버전·청크 상태를, `list_documents`는 접근 가능한 문서 메타데이터를 반환한다. `create_document`는 `title`·`content`·`content_type`(`txt`·`md`)·`tags`·`visibility`·`grant_users`·`grant_groups`를 받아 기존 텍스트 진입점으로 공급한다. stdio의 사용자 컨텍스트는 툴 인자가 아니라 `MCP_USER_ID` 환경변수로 고정한다. 미설정 시 public 문서 읽기는 허용하지만 소유자를 확정할 수 없어 쓰기는 거부한다 (ADR-025, ADR-036).

원격 MCP는 API 앱의 `/mcp`에서 Streamable HTTP로 동작한다(ADR-056, 구현 #188). 주체는 매 요청 검증한 Bearer 토큰에서만 정하며 `MCP_USER_ID`는 stdio 전용이다. 읽기 3개는 모든 유효 토큰에, 생성은 `read_write` 사용자 토큰에만 열린다. 공유 토큰은 공유에 넣은 문서만 읽는다. 두 transport는 같은 서비스·열람 술어·도구 본체를 사용하며, 원격은 API 풀과 예열된 프로바이더를 공유한다.

API 토큰(위임·공유)의 해석은 `services/auth.py`의 `validate_token` 한 곳이다 — REST(`api/deps.py current_user`)와 원격 MCP의 Bearer 미들웨어가 같은 함수를 쓴다. 조회 SQL이 `expires_at IS NULL OR expires_at > now()`로 만료를 판정하므로 만료 토큰은 폐기·틀린 값과 같은 `AuthenticationFailed`(401)다. 인증에 성공하면 같은 트랜잭션에서 `last_used_at`을 갱신하되 1분 안의 중복 갱신은 건너뛰고 잠긴 행은 기다리지 않는다(`FOR UPDATE SKIP LOCKED`) — REST 요청 트랜잭션이 응답 직전까지 열려 있어, 기다리면 같은 토큰의 동시 요청이 줄을 선다. 롤백된 요청의 사용은 남지 않는다 (034, ADR-061 결정 1).

`POST /api/search`도 같은 근거 필드(`filename`·`based_on_version`)를 함께 내려준다. 서비스가 하나여도 두 경로의 응답 스키마가 갈라지면 "REST와 MCP의 결과가 같다"가 깨진다 — `tests/test_mcp_server.py`가 두 응답을 직접 비교해 이를 지킨다.

## DB 스키마

```sql
-- documents: 정형 메타데이터 + 버전 + 권한 (하이브리드 활용의 절반)
CREATE TABLE documents (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title            text NOT NULL,
  filename         text,                   -- 원본 파일명. 원본이 있으면 최신 판(document_files)의 파일명과 같게 유지된다 (ADR-046)
  content_type     text NOT NULL,          -- pdf | docx | txt | md | hwp | hwpx | xlsx | pptx | png | jpg | jpeg
  content          text NOT NULL,          -- 문서 텍스트 (현재 버전). 편집·버전 관리·임베딩의 대상
  content_hash     text NOT NULL,          -- sha256, 트리거의 변경 감지 기준
  version          int  NOT NULL DEFAULT 1,
  owner_id         text NOT NULL,
  visibility       text NOT NULL DEFAULT 'public',  -- public | private
  tags             text[] NOT NULL DEFAULT '{}',
  embedding_status text NOT NULL DEFAULT 'pending', -- pending|processing|ready|error
  extraction_status text NOT NULL DEFAULT 'done',   -- pending|failed|done (021, ADR-052). OCR 추출 상태
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),

  -- 추출이 끝났다고 표시된(done) 문서가 빈 본문인 상태를 DB에서 차단한다 (002 → 021에서 조건부로).
  -- 빈 본문은 임베딩할 것이 없어 검색에 영원히 잡히지 않는 유령 행이 된다.
  -- 추출 중(pending)·인식 실패(failed) 문서는 빈 본문으로 존재할 수 있다 — 스캔 문서는
  -- 빈 텍스트로 먼저 생기고 워커가 OCR 결과를 채운다 (ADR-052 결정 5).
  -- 제거 문자를 명시한다: btrim의 1인자 형태는 공백만 제거해 탭·개행만 남은 본문이
  -- 그대로 통과하는데, 텍스트 레이어 없는 PDF의 추출 결과가 정확히 그 형태다 (M1에서 실측).
  CONSTRAINT documents_content_not_blank
    CHECK (extraction_status <> 'done' OR length(btrim(content, E' \t\r\n\f')) > 0),
  -- 030 (ADR-054): 폴더와 범위 상속. 기존 문서는 folder_id NULL이라 자기 범위 그대로다
  folder_id      uuid REFERENCES folders(id) ON DELETE RESTRICT,   -- 빈 폴더만 지운다
  follows_folder boolean NOT NULL DEFAULT true,  -- true = 「폴더 범위 따름」, false = 「개별 지정」
  -- 032 (ADR-060): 휴지통. 값이 있으면 열람 술어가 전 경로에서 뺀다. 청크·관계·버전·잡은 그대로라 복원은 이 값을 지우는 것뿐
  deleted_at     timestamptz                    -- 휴지통 이동 시각. TRASH_RETENTION_DAYS가 지나면 워커가 영구 삭제(하드 DELETE)
);

-- document_versions: 문서 텍스트의 버전 이력 (append-only)
-- 파일 버전 이력이 아니다. v1으로 되돌려도 원본 파일이 아니라 v1 시점의 문서 텍스트가 나온다.
CREATE TABLE document_versions (
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version      int  NOT NULL,
  content      text NOT NULL,
  content_hash text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  author       text, -- 사용자 이름 스냅샷, FK 없음 (038)
  author_via   text,
  PRIMARY KEY (document_id, version),
  CONSTRAINT document_versions_author_via_valid CHECK (author_via IN (
    'session', 'token', 'mcp', 'cli', 'share', 'worker', 'direct'
  ))
);

-- document_files: 업로드된 원본 파일의 판 이력 (018, ADR-046). append-only — 교체는 새 판이다
-- documents에 bytea를 두지 않는다: 목록·검색·상세와 행 갱신이 수십 MB 원본과 한 행으로 묶이지 않게
CREATE TABLE document_files (
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  file_version int  NOT NULL CHECK (file_version >= 1),
  filename     text NOT NULL,
  data         bytea NOT NULL CHECK (octet_length(data) > 0),
  size         bigint GENERATED ALWAYS AS (octet_length(data)) STORED,        -- DB가 계산한다
  sha256       text   GENERATED ALWAYS AS (encode(sha256(data), 'hex')) STORED,
  text_version int,                -- 등록(업로드·교체) 시점의 텍스트 버전. 재추출 버전과는 연결되지 않는다.
                                   -- NULL = 이 판의 텍스트가 아직 추출되지 않았다(추출 중 문서, 021).
                                   -- 워커가 첫 텍스트(v1)를 쓰는 트랜잭션에서 채운다
  uploaded_by  text NOT NULL,
  uploaded_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (document_id, file_version),
  FOREIGN KEY (document_id, text_version)   -- CASCADE 없음: 텍스트 버전 정리가 원본 판을 지우지 못한다
    REFERENCES document_versions (document_id, version)
);

-- document_file_previews: 원본 판의 PDF 변환본 (036, ADR-058 개정). 판 하나당 하나.
-- 파생물이다 — 018의 원본 등록은 공급 자체라 트리거가 없지만, 변환본 행과 잡은 037의 트리거가 만든다.
-- 편집·버전·감사 대상이 아니고 휴지통·열람 조건을 따로 두지 않는다(미리보기 경로가 ensure_visible을 먼저 부른다)
CREATE TABLE document_file_previews (
  document_id  uuid NOT NULL,
  file_version int  NOT NULL,
  status       text NOT NULL,    -- pending | ready | failed | unavailable (아래 「미리보기 변환 잡」)
  pdf          bytea,            -- status = 'ready'일 때만, 비어 있지 않다 (CHECK)
  error        text,             -- failed·unavailable의 이유
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (document_id, file_version),
  FOREIGN KEY (document_id, file_version)       -- 원본 판이 지워지면(문서 영구 삭제) 함께 지워진다
    REFERENCES document_files (document_id, file_version) ON DELETE CASCADE,
  CHECK (status IN ('pending', 'ready', 'failed', 'unavailable')),
  CHECK ((status = 'ready') = (pdf IS NOT NULL)),
  CHECK (pdf IS NULL OR octet_length(pdf) > 0)
);

-- idempotency_keys: 문서 생성 요청의 멱등키 (019, ADR-047). 문서 INSERT와 같은 트랜잭션에서 앱이 넣는다
-- 파생물이 아니라 요청의 기록이라 트리거 규칙의 대상이 아니다. 24시간 뒤 워커 스윕이 지운다
CREATE TABLE idempotency_keys (
  owner_id     text NOT NULL,                  -- 소유자 범위: 남의 같은 키와 부딪히지 않고 존재도 드러나지 않는다
  key          text NOT NULL CHECK (length(key) BETWEEN 1 AND 255),
  request_hash text NOT NULL,                  -- 같은 키에 다른 요청이면 422
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,  -- 키와 문서는 함께 있거나 함께 없다
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id, key)                  -- 같은 키의 동시 요청을 직렬화한다
);

-- groups·group_members·document_grants: 열람 부여 (025, ADR-044). private = 소유자 + 부여 대상
CREATE TABLE groups (
  id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL UNIQUE                    -- 부서·팀. 관리자가 만든다
);
CREATE TABLE group_members (
  group_id uuid NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  user_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, user_id)
);
-- 공유 스키마의 구현 계약: 026_shares_tables.sql (ADR-044 #97 c)
CREATE TABLE shares (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (owner_user_id, name)
);
CREATE TABLE document_grants (                 -- 문서 → 대상의 읽기. 편집은 소유자만
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  user_id     uuid REFERENCES users(id) ON DELETE CASCADE,
  group_id    uuid REFERENCES groups(id) ON DELETE CASCADE,
  share_id    uuid REFERENCES shares(id) ON DELETE CASCADE, -- 026: public/private 모두 허용
  CHECK (num_nonnulls(user_id, group_id, share_id) = 1)  -- 다형 칼럼 대신 종류별 칼럼: FK가 고아 부여를 막는다
);

-- folders·folder_grants: 폴더 트리와 열람 부여 (030, ADR-054). 범위는 최상위 폴더만 갖는다
-- 폴더 이동은 없어 최상위 조상은 만든 뒤 바뀌지 않는다. 실효 범위는 저장하지 않는다
CREATE TABLE folders (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  parent_id  uuid REFERENCES folders(id) ON DELETE RESTRICT,
  name       text NOT NULL,        -- 공백뿐이 아니고 '/'를 포함하지 않는다 (CHECK)
  created_by text NOT NULL,        -- 사용자명. 이 계정은 삭제를 거부한다
  visibility text,                 -- public | private — 최상위만
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT folders_root_scope CHECK ((parent_id IS NULL) = (visibility IS NOT NULL))
);
-- 같은 부모 아래 하위 폴더만 이름이 유일하다. 최상위 이름 충돌 오류는 남의 제한 폴더를 누출한다
CREATE UNIQUE INDEX uq_folders_parent_name ON folders (parent_id, name) WHERE parent_id IS NOT NULL;
CREATE TABLE folder_grants (     -- 최상위 폴더 → 사용자·그룹의 읽기. 외부 공유 부여는 없다
  folder_id uuid NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  user_id   uuid REFERENCES users(id) ON DELETE CASCADE,
  group_id  uuid REFERENCES groups(id) ON DELETE CASCADE,
  CHECK (num_nonnulls(user_id, group_id) = 1)
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
  kind            text NOT NULL DEFAULT 'embed',   -- embed|edges|extract|preview (016·021·036, ADR-029·052·058)
  status          text NOT NULL DEFAULT 'pending', -- pending|processing|done|error
  attempts        int  NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  last_error      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  started_at      timestamptz,
  finished_at     timestamptz,
  -- 선점 lease. heartbeat가 연장하고, 만료되면 스윕이 회수한다 (020, ADR-050)
  lease_expires_at timestamptz,
  CONSTRAINT embedding_jobs_processing_has_lease
    CHECK (status <> 'processing' OR lease_expires_at IS NOT NULL)
);

-- 핵심: 문서·종류당 pending 잡은 1개만 — DB 계층 코얼레싱 (016에서 종류를 키에 넣었다)
CREATE UNIQUE INDEX uq_pending_job_per_doc_kind
  ON embedding_jobs(document_id, kind) WHERE status = 'pending';

-- document_edges: 저장 시점에 만드는 관계 그래프 (006, ADR-029)
-- ★ 저장은 단방향(src = 계산 주체, 재계산은 자기 src 행만 교체), 조회는 src ∪ dst로 대칭 (014, ADR-029 개정)
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
  target_title    text NOT NULL    -- ★ 저장 시 제목으로 정규화(015 · 표 셀 \| 023, ADR-030),
                                    --   해석은 조회 시점에 조회자의 열람 범위에서
);

-- users·sessions: 최소 로그인 (009)
CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username      text NOT NULL UNIQUE CHECK (username NOT LIKE 'share:%'), -- 026: 공유 주체 접두사 예약
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
  user_id    uuid REFERENCES users(id) ON DELETE CASCADE,
  share_id   uuid REFERENCES shares(id) ON DELETE CASCADE, -- 026
  name       text NOT NULL,
  token_hash text NOT NULL UNIQUE,   -- sha256(원문). 원문은 발급 응답에만 반환
  scope      text NOT NULL CHECK (scope IN ('read', 'read_write')),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz,          -- 034: NULL = 만료 없음. 과거 값 거부는 발급 서비스가 한다 (ADR-061 결정 1)
  last_used_at timestamptz,          -- 034: 인증에 성공한 요청이 1분 단위로 갱신한다
  CHECK (num_nonnulls(user_id, share_id) = 1), -- 026: 사용자 또는 공유 하나
  CHECK (share_id IS NULL OR scope = 'read')
);

-- audit_log: 사건 시점의 감사 기록 (028, ADR-055). 앱은 INSERT하지 않는다 — 029의 트리거·함수가 쓴다
-- UPDATE·DELETE·TRUNCATE는 029의 트리거가 거부한다(테이블 소유자인 앱 롤에도)
CREATE TABLE audit_log (
  id             bigserial PRIMARY KEY,        -- 정렬·커서 기준 (같은 트랜잭션의 행은 occurred_at이 같다)
  occurred_at    timestamptz NOT NULL DEFAULT now(),
  action         text NOT NULL,                -- CHECK 15종 (아래 「감사 로그」 절 — 029 7종 + 031·033·035·040·041)
  actor          text,                         -- 사용자명 스냅샷. 앱 행위자가 없으면 NULL
  actor_via      text,                         -- session|token|mcp|cli|share|worker, 직접 SQL이면 NULL
  db_role        text NOT NULL DEFAULT current_user,
  document_id    uuid,                         -- FK 없음: 문서가 지워져도 기록은 남는다
  document_title text,                         -- 제목 스냅샷
  detail         jsonb NOT NULL DEFAULT '{}'
);
```

`026_shares_tables.sql`은 기존 마이그레이션을 수정하지 않고 위 공유 스키마를 추가한다.
`document_grants`의 대상 CHECK는 세 칸으로 교체하고, `(document_id, share_id)` 부분 유니크
인덱스와 `share_id` 조회 인덱스를 각각 `WHERE share_id IS NOT NULL`로 둔다. 공유 삭제는
토큰·부여를 함께 지우며, 열람 범위 교체는 사용자·그룹 부여만 바꾸고 공유 부여는 유지한다.

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
  - → **2026-10-06 개정 (ADR-011, #187)**: 검색 트랜잭션에 `SET LOCAL hnsw.iterative_scan = strict_order`를 건다. 폴더로 열람 범위가 좁아진 사용자의 recall@10이 0.40 → 1.00, 검색 폴더 필터가 0.27 → 0.99가 됐다. 순서를 보장하지 않는 `relaxed_order`는 쓰지 않는다. `test_indexes.py`·`test_search.py`가 이 설정과 좁은 범위의 recall을 고정한다.

> **HNSW 가용성은 확정됐다.** 배포판에 pgvector **0.8.1**이 번들되어 있고(`docs/OPENSQL_RESEARCH.md` §0), 실 VM에서 `CREATE INDEX ... USING hnsw`와 검색 계획의 인덱스 사용을 실측했다(§12). ADR-002가 대비해 둔 pgvectorscale·IVFFlat 전환 경로는 쓰지 않는다.

## 자동 임베딩 파이프라인 (DB 계층)

### 트리거

```sql
CREATE OR REPLACE FUNCTION on_document_content_changed() RETURNS trigger AS $$
DECLARE
  v_actor text := NULLIF(current_setting('openarchive.actor_id', true), '');
  v_via text := NULLIF(current_setting('openarchive.actor_via', true), '');
  v_source text := NULLIF(current_setting('openarchive.text_source', true), '');
BEGIN
  -- (1) 버전 이력 기록 — INSERT의 v1도 포함해 append-only 이력을 완성한다
  INSERT INTO document_versions (document_id, version, content, content_hash, author, author_via)
  VALUES (NEW.id, NEW.version, NEW.content, NEW.content_hash,
          CASE WHEN v_source = 'extraction' THEN NULL ELSE v_actor END,
          CASE WHEN v_source = 'extraction' THEN 'worker' ELSE COALESCE(v_via, 'direct') END)
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
  WHEN (pg_trigger_depth() = 0           -- 트리거 내부 UPDATE로 인한 재귀 방지
        AND NEW.extraction_status = 'done') -- 추출 중 문서는 발화하지 않는다 (022, ADR-052)
  EXECUTE FUNCTION on_document_content_changed();
```

**버전 이력도 DB 계층이 책임진다.** 애플리케이션은 `document_versions`에 직접 INSERT하지 않는다 — `embedding_jobs`와 같은 원칙이다.

- **INSERT 시**: `version=1` 행이 이력에 기록된다. 문서 생성 직후부터 v1 조회가 가능하다.
- **PUT 시**: API는 `documents`의 `version`(+1), `content`, `content_hash`만 UPDATE한다. 이력 기록은 트리거가 **같은 트랜잭션에서** 수행하므로, 본문만 바뀌고 이력이 누락되는 상태가 구조적으로 불가능하다.
- `ON CONFLICT (document_id, version) DO NOTHING`은 재실행 안전장치다. 같은 버전 번호로 트리거가 두 번 발화해도 이력이 중복되지 않는다.
- **추출 중 문서(`extraction_status <> 'done'`)에서는 발화하지 않는다.** 스캔 문서는 빈 텍스트로 INSERT되는데, 여기서 발화하면 빈 v1이 이력에 남고 청크가 나올 수 없는 임베딩 잡이 돈다. v1은 워커가 첫 텍스트를 쓰는 UPDATE가 기록한다 (아래 「추출 잡」).

**텍스트 버전 작성자(038·039, ADR-061 결정 6).** 트리거가 위 GUC에서 `author`·`author_via`를 채운다.
빈 placeholder는 `NULLIF(..., '')`로 읽는다. 추출 출처이면 NULL·`worker`, 아니면 행위자 이름과 경로를 쓰고
경로가 없으면 `direct`다. OCR은 워커 경로로 기록하고, 동기 재추출은 `mark_text_from_extraction`을 새 텍스트 쓰기 직전에
호출한다. 감사 행위자는 바꾸지 않아 재추출을 요청한 사람도 별도로 남는다. 작성자 원칙은 **행동 기준**이다 — 업로드·원본
교체·편집·복원은 요청한 사람, OCR 반영·재추출은 워커다(스캔 파일 업로드·교체의 텍스트 버전은 비동기 OCR이 써서 워커, ADR-061 결정 6).

| GUC | 값 · 전달 규칙 |
|---|---|
| `openarchive.actor_id` | 사용자 이름 또는 빈 값 — 버전 작성자와 감사 행위자의 이름 |
| `openarchive.actor_via` | `session`·`token`·`mcp`·`cli`·`share`·`worker` — 행위자의 경로 |
| `openarchive.text_source` | `extraction`이면 재추출이 다시 만든 텍스트 — 버전 작성자는 워커, 감사 행위자는 유지 |

모두 `set_config(..., true)`로 트랜잭션 범위에만 전달한다. 세션 `SET`은 쓰지 않는다.
038은 기존 행을 같은 문서와 `occurred_at = created_at`인 감사 행으로 채운다. v1은 `document_created`, v2 이상은
`text_updated`의 `detail.version`까지 맞추며 여러 짝이면 최소 감사 id를 쓴다. 짝 없는 행은 NULL·NULL이다.
스캔 문서 v1은 생성과 시각이 달라 짝이 없을 수 있고, 과거 동기 재추출은 당시 감사 행위자로 남는 한계가 있다.

화면·CLI는 다음 순서로 작성자를 표시한다. 서버는 문구를 만들지 않고 nullable 두 필드를 반환한다.

| 조건 (위에서 먼저 적용) | 표시 |
|---|---|
| `author`가 있음 | 사용자 이름 |
| 이름 없음 · `author_via = 'worker'` | 워커 |
| 이름 없음 · `author_via = 'direct'` | 직접 접속 |
| 이름 없음 · `author_via = 'cli'` | 운영자 CLI |
| 그 밖에 이름 없음(NULL 포함) | 기록 없음 |

### 감사 로그 — 트리거가 같은 트랜잭션에서 남긴다 (ADR-055)

"누가 무엇을 바꿨나"도 잡 생성과 같은 원칙이다 — 기록이 빠지는 경로가 DB 밖에 없어야 한다. 쓰기는 대상 테이블의 AFTER 트리거가, 원본 내려받기는 DB 함수가 **원래 작업과 같은 트랜잭션에서** `audit_log`에 쓴다. 쓰기가 롤백되면 기록도 함께 사라진다. INSERT 지점은 함수 `audit_record()` 하나다(`029_audit_triggers.sql`).

| 트리거 · 함수 | 대상 · 조건 | `action` · `detail` |
|---|---|---|
| `trg_audit_document_created` / `trg_audit_document_deleted` | `documents` INSERT / DELETE | `document_created` / `document_deleted` · `{}` |
| `trg_audit_document_trash_changed` | `documents` UPDATE OF `deleted_at`, NULL↔값 전이일 때 (033) | NULL→값 `document_trashed`(휴지통 이동) / 값→NULL `document_restored`(복원) · `{}`. 영구 삭제는 위 `document_deleted`다 |
| `trg_audit_visibility_changed` | `documents` UPDATE OF `visibility`, 값이 바뀔 때 | `access_changed` · `{kind: visibility, before, after}` |
| `trg_audit_text_updated` | `document_versions` INSERT, v2 이상 | `text_updated` · `{version}` |
| `trg_audit_original_replaced` | `document_files` INSERT, 2판 이상 | `original_replaced` · `{file_version}` |
| `trg_audit_grant_changed` | `document_grants` INSERT / DELETE — 공유 부여·연쇄 삭제 제외 | `access_changed` · `{kind: grant, change, grantee_type, grantee}` |
| `trg_audit_group_member_changed` | `group_members` INSERT / DELETE — 연쇄 삭제 제외 | `group_member_changed` · `{change, group, user}` |
| `record_original_download(document_id, file_version)` | 원본 판을 읽는 트랜잭션에서 `get_original_file`이 호출 | `original_downloaded` · `{file_version}` |
| `record_original_preview(document_id, file_version)` | 미리보기 경로에서 `get_original_file(preview=True)`가 형식·변환본 상태 확인 뒤 같은 트랜잭션에서 호출 (035) | `original_previewed` · `{file_version}` — **200일 때만** 기록한다. 허용 밖 형식·변환본 없음(415)·변환 실패(415)·준비 중(409)은 기록하지 않는다. 변환본을 보낸 경우도 detail은 같다 |
| `trg_audit_folder_visibility_changed` | `folders` UPDATE OF `visibility`, 값이 바뀔 때 (031) | `folder_access_changed` · `{kind: visibility, folder_id, folder_name, before, after}` |
| `trg_audit_folder_grant_changed` | `folder_grants` INSERT / DELETE — 연쇄 삭제 제외 (031) | `folder_access_changed` · `{kind: grant, change, grantee_type, grantee, folder_id, folder_name}` |
| `trg_audit_document_folder_changed` | `documents` UPDATE OF `follows_folder`·`folder_id` (031) | `access_changed` · `{kind: inherit, before, after}`(`folder`/`own`) · 「폴더 범위 따름」 문서의 이동은 `{kind: folder, before, after}`(폴더 이름) |
| `trg_audit_document_owner_changed` / `trg_audit_folder_owner_changed` | `documents` UPDATE OF `owner_id` / `folders` UPDATE OF `created_by`, 실제 값 변경만 (040) | `owner_changed`(소유자 변경) · 문서 `{kind: document, before, after}`, 폴더 `{kind: folder, folder_id, folder_name, before, after}`. 폴더는 대상 문서 id·제목 NULL |
| `trg_audit_share_changed` | `shares` INSERT / DELETE — 연쇄 삭제 제외 (041) | `share_changed` · `{change: created/deleted, share_id, share_name, owner}` |
| `trg_audit_share_grant_changed` | `document_grants` INSERT / DELETE — `share_id IS NOT NULL`, 연쇄 삭제 제외 (041) | `share_changed` · `{change: document_added/document_removed, share_id, share_name, owner}` · 대상 문서 id·제목 |
| `trg_audit_share_token_changed` | `api_tokens` INSERT / DELETE — `share_id IS NOT NULL`, 연쇄 삭제 제외 (041) | `share_changed` · `{change: token_issued/token_revoked, share_id, share_name, owner, token_name}` |
| `trg_audit_group_changed` / `trg_audit_user_changed` | `groups` / `users` INSERT / DELETE (041) | `group_changed` · `{change: created/deleted, group}` / `user_changed` · `{change: created/deleted, user}` |
| `trg_audit_log_reject_change` / `trg_audit_log_reject_truncate` | `audit_log` UPDATE·DELETE(행) / TRUNCATE(문) | 예외 — 거부 |

- **부여 대상(사용자·그룹)의 추가·제거도 「열람 범위 변경」이다.** 「제한」 문서의 열람자는 부여 행으로 바뀐다. 그래서 `set_access`는 부여를 전량 교체하지 않고 **차이만** DELETE·INSERT한다 — 바뀌지 않은 대상이 「제거→추가」로 기록되지 않게.
- **연쇄 삭제는 건너뛴다.** 문서·그룹·사용자를 지울 때 함께 지워지는 부여·구성원 행은 부모가 이미 없으므로 기록하지 않는다 — 실제 사건(문서 삭제)이 「부여 제거」 기록에 덮이지 않게. 그 대가로 그룹·사용자 삭제로 사라진 권한은 감사 로그에 없다(ADR-055 트레이드오프 6).
- 공유 삭제는 `share_changed`의 `deleted` 1건, 사용자 삭제는 `user_changed` 1건만 남는다. 사용자 토큰 발급·폐기는 제외한다. 공유 부여는 `access_changed`가 아니라 `share_changed`다. `share_id`는 UUID 문자열, `owner`는 공유 주인 사용자명이고 공유 문서 부여 이외의 새 동작은 대상 문서 id·제목이 NULL이다.
- 감사 행은 문서에 FK를 걸지 않고 제목·사용자명을 스냅샷으로 둔다. 문서를 지워도 그 문서의 기록과 제목이 남는다.

**행위자 전달 흐름.** 진입점이 트랜잭션 안에서 `services/audit.py`의 `set_actor`를 부르면, 그것이 트랜잭션 범위 GUC 세 개(`openarchive.actor_id`·`actor_via`·`share_id`)를 `set_config(…, true)`로 건다. 트리거의 `audit_record()`가 그 값을 `NULLIF(current_setting(name, true), '')`로 읽는다 — HA 풀 백엔드에서는 값이 없을 때 NULL이 아니라 `''`이 온다.

| 진입점 | `actor` | `actor_via` |
|---|---|---|
| REST — 세션 / 사용자 API 토큰 (`api/deps.py` `current_user`) | 사용자명 | `session` / `token` |
| REST — 공유 토큰 | NULL (`detail`에 `share_id`·`share_name`) | `share` |
| stdio MCP `create_document` | `MCP_USER_ID` | `mcp` |
| 원격 MCP `create_document` | Bearer 토큰 주인 | `mcp` |
| 운영자 CLI `import`·`demo` | `--user` | `cli` |
| 운영자 CLI `reextract` | NULL | `cli` |
| 워커 — OCR 결과 반영(재추출 v2 이상) | NULL | `worker` |
| psql 등 직접 SQL | NULL (`db_role`만) | NULL |

`set_actor`는 autocommit 유휴 상태(트랜잭션 밖)에서 부르면 예외다. 세션 `SET`은 OpenProxy 풀 백엔드를 타고 다음 클라이언트로 새므로(HA 실측 75/100, `OPENSQL_RESEARCH.md` §5-3) 쓰지 않는다 — GUC 이름이 이 파일 밖에 나오거나 세션 `SET`을 쓰는 코드는 `test_architecture.py`가 막는다.

### 추출 잡 — 스캔 문서는 텍스트 없이 먼저 생긴다

이미지(`png`·`jpg`·`jpeg`)와 텍스트 레이어가 빈 쪽이나 글자 정보가 깨진 쪽이 있는 PDF는 tesseract로 OCR한다(`kor+eng`, `--psm 4`, PDF는 그 쪽만 300dpi로 래스터화하고 나머지 쪽은 레이어 텍스트를 쪽 순서대로 잇는다 — `services/parsing.py`). 쪽당 수 초라 업로드 요청 안에서 끝낼 수 없으므로 **추출도 워커 잡이 한다** (ADR-052). 글자 정보가 깨진 쪽은 ToUnicode 없는 Type0·`Identity` 글꼴이나 Adobe 글리프 목록에 없는 `/Differences` 이름을 쓰는 쪽이다 — 텍스트가 아니라 글꼴 구조로 판정한다(#191, ADR-052 결정 2). 모든 쪽을 글자로 읽을 수 있는 PDF와 나머지 형식은 지금처럼 요청 안에서 동기로 추출한다.

```sql
-- 추출 중으로 들어오거나(새 스캔 문서) 추출 중으로 바뀌면(OCR 대상의 재추출·원본 교체) 추출 잡을 남긴다 (022)
CREATE TRIGGER trg_documents_extraction_requested
  AFTER INSERT OR UPDATE OF extraction_status ON documents
  FOR EACH ROW
  WHEN (pg_trigger_depth() = 0 AND NEW.extraction_status = 'pending')
  EXECUTE FUNCTION on_document_extraction_requested();   -- INSERT INTO embedding_jobs (document_id, kind) VALUES (NEW.id, 'extract') + NOTIFY
```

1. **업로드**: OCR 대상이면 문서를 빈 문서 텍스트 + `extraction_status = 'pending'`으로 INSERT하고, 원본 1판을 `text_version = NULL`로 같은 트랜잭션에 저장한다. 트리거가 추출 잡을 만든다 — 앱은 `embedding_jobs`에 INSERT하지 않는다. 문서는 즉시 목록·상세에 보이지만 청크가 없어 검색·관계·군집에는 없다.
2. **워커**: 추출 잡을 집어 트랜잭션 안에서 최신 원본 판을 읽고, 트랜잭션 **밖**(`asyncio.to_thread`)에서 OCR한 뒤, 문서 행을 잠그고 잡 소유(`lock_owned_job`)를 확인한 트랜잭션에서 `apply_extracted_text`로 반영한다. 본문·`content_hash`·`extraction_status = 'done'`을 **한 UPDATE**로 쓰므로 위 003 트리거가 그 자리에서 텍스트 버전·임베딩 잡을 잇는다 — 나눠 쓰면 트리거가 `done`을 보지 못해 조용히 끊긴다. 첫 추출이면 v1이 되고 원본 판의 `text_version`을 채우며, 재추출이면 버전이 하나 오른다. 결과가 이전 텍스트와 같으면 `done`만 표시하고 새 버전을 만들지 않는다.
3. **인식 실패**: OCR 결과가 비었거나 500KB를 넘으면 **재시도하지 않고** `extraction_status = 'failed'`로 끝낸다 — 결정적이라 다시 해도 같다. 예외(tesseract 비정상 종료 등)는 다른 잡처럼 백오프 재시도하고, 예산을 소진하면(`fail_job`·좀비 스윕 모두) `failed`로 표시한다. `embedding_status`는 건드리지 않는다 — 인식 실패와 임베딩 실패는 사용자가 할 일이 다르다(원본 교체 vs 재임베딩). 새 문서는 빈 텍스트로, 재추출이던 문서는 이전 텍스트로 남는다.
4. **재추출·원본 교체**: 대상이 OCR 대상이면 텍스트를 쓰지 않고 `extraction_status = 'pending'`으로만 바꾼다(교체는 파일명·유형과 한 UPDATE). 추출이 끝날 때까지 이전 텍스트·청크로 검색된다 — 재임베딩과 같은 원칙이다.
5. **추출 중 잠금**: 추출 중인 문서의 편집·되돌리기·재추출·원본 교체는 409다. 워커 결과가 사람이 고친 텍스트를 덮지 않게 한다. 인식 실패로 텍스트가 빈 문서는 편집·되돌리기만 409이고, 원본 교체·재추출로 다시 시도할 수 있다. 태그·제목·공개범위·삭제는 막지 않는다.

**한계**: 일부러 비운 쪽이 있는 텍스트 PDF도 「텍스트 인식 중」을 거치고, 쪽 번호 같은 텍스트가 얹힌 스캔 쪽은 빈 쪽이 아니라서 인식하지 않는다 (ADR-052 트레이드오프 1). OCR 정확도는 한국어 보도자료 래스터화 실측에서 CER 0.068(깨끗한 판)·0.093(열화판), 쪽당 약 3.3초(맥 M2 Pro · tesseract 5.5)이며, 그 오류는 문서 텍스트에 그대로 남아 편집으로 고친다. Rocky 9 패키지(tesseract 4.1.1 + langpack-kor 4.1.0)는 `rockylinux:9` 컨테이너 실측에서 CER 0.031~0.050·쪽당 3.4~5.5초였다(#135 코멘트, arm64 컨테이너라 x86 호스트 시간과는 다를 수 있다).

### 미리보기 변환 잡 — 한글·오피스 원본을 격리된 변환기로 PDF로 바꾼다 (ADR-058 개정, #228)

브라우저가 열지 못하는 HWP·HWPX·DOCX·XLSX·PPTX 원본 판은 워커가 PDF로 바꿔 `document_file_previews`에 둔다.
HWP·HWPX는 rhwp, 오피스 3종은 LibreOffice이고 둘 다 bubblewrap 안에서 돈다(`services/preview.py`). 변환기 설치는
`OPERATIONS.md` 「원본 미리보기 변환기」.

```sql
-- 변환 대상 판정은 이 함수 한 곳이다 (037). 파이썬 PREVIEW_CONVERTIBLE_EXTENSIONS는 테스트로 대조한다
CREATE FUNCTION preview_convertible(filename text) RETURNS boolean LANGUAGE sql IMMUTABLE …;
                                              -- 확장자(대소문자 무관)가 hwp·hwpx·docx·xlsx·pptx

CREATE TRIGGER trg_document_files_preview_requested
  AFTER INSERT ON document_files              -- 업로드·원본 교체가 판을 쌓을 때
  FOR EACH ROW WHEN (preview_convertible(NEW.filename))
  EXECUTE FUNCTION on_document_file_preview_requested();
  -- 문서 행을 FOR UPDATE로 잠그고 → 변환본 행 'pending' → embedding_jobs (document_id, 'preview')
  -- (uq_pending_job_per_doc_kind로 코얼레싱) → NOTIFY
```

- **판은 바뀌지 않으므로 판마다 한 번 변환한다.** 잡은 문서 단위이고, 워커는 그 문서에서 `pending`인 판을 판 번호순으로
  하나씩 변환해 **판마다 따로 커밋**한다(`finalize_preview` — 문서 행을 잠그고 잡 소유를 확인한 뒤 `status = 'pending'`인
  행만 바꾼다). 마지막 판 뒤에 잡을 `done`으로 마감한다. 앱은 변환본 행과 잡을 INSERT하지 않고, 워커는 상태·PDF만 UPDATE한다.
  PDF는 원본 INSERT와 같이 `%b`·서버 바인딩 커서로 보낸다(hex 리터럴이면 두 배 크기로 OpenProxy를 지난다 — ADR-062 결정 2 개정).
- **선점은 후순위다.** `claim_job`이 `ORDER BY (q.kind = 'preview'), q.id`로 집는다 — 변환이 임베딩·관계·추출을 앞지르지
  않는다. 다른 종류의 id 순서는 그대로다. 이미 집은 변환이 도는 동안 같은 워커의 다음 잡은 기다린다(OCR과 같은 한계).
- **변환은 트랜잭션 밖이다.** 판 바이트와 그 판의 문서 텍스트(`text_version`의 버전 본문, NULL이면 현재 `documents.content`)를
  읽고, `asyncio.to_thread`로 `convert_to_pdf`를 부른다. lease를 잃으면 결과를 쓰지 않는다(ADR-050).

| 변환본 상태 | 만드는 곳 | 의미 |
|---|---|---|
| `pending` | 트리거(업로드·교체) · `enqueue_all_preview_jobs()` | 변환 대기. 같은 문서에 `kind='preview'` 잡이 있다 |
| `ready` | 워커 | `pdf` 있음 |
| `failed` | 워커 | 다시 해도 같은 결과(`PreviewRenderFailed` — 결과 PDF 없음·읽을 수 없음·쪽 없음·한글이 그려지지 않음)라 재시도 없이. 또는 예외 재시도 예산 소진(`fail_job`·좀비 스윕) |
| `unavailable` | 워커 | `ConverterUnavailable` — 변환기 실행 파일·한글 글꼴(`fc-list :lang=ko`)·격리 중 하나가 없다. 설치 뒤 `openarchive rebuild-previews`로 다시 건다 |

- **예외 분류**: 시간 초과(`PREVIEW_TIMEOUT_SECONDS`, 기본 900초 — 프로세스 그룹을 SIGKILL)·변환기 비정상 종료는 다른 잡처럼
  지수 백오프 재시도하고, 예산(3회)을 소진하면 그 문서의 `pending` 판이 `failed`가 된다. `documents`의 임베딩·추출 상태와
  이미 `ready`인 판은 건드리지 않는다. **예외가 난 판은 미뤄 두고 나머지 판을 끝까지 변환한 뒤** 첫 예외로 재시도한다 — 곧바로
  올리면 재시도마다 같은 판에서 멈춰 뒤의 판(최신 판 포함)이 시도도 못 한 채 소진 때 함께 `failed`가 된다. 그래서 재시도와
  소진은 실제로 실패하는 판에만 닿는다.
- **한글 렌더 검사(두 겹)**: 변환 전 `fc-list :lang=ko family`가 비면 `unavailable`. 변환 뒤 문서 텍스트에 한글(U+AC00–U+D7A3)이
  있는데 결과 PDF의 텍스트 레이어(pypdf)에 한글이 없으면 `failed`. 종료 코드 0을 성공으로 믿지 않는다 — 글꼴이 없으면 rhwp는
  빈칸, LibreOffice는 □를 그리고 0으로 끝난다.
- **격리(ADR-058 결정 9)**: bwrap `--unshare-net --unshare-user --unshare-ipc --unshare-uts --unshare-pid --die-with-parent
  --new-session --clearenv`, 읽기 전용 `/usr`·`/bin`·`/lib`·`/lib64`와 글꼴·인증서 등 몇 개의 `/etc` 파일, 빈 `/tmp`,
  읽기 전용 입력 파일 하나, 쓰기 가능한 결과 디렉터리 하나만 보인다. `/usr` 밖의 변환기는 그 파일 하나만 `/converter`로 노출한다.
  LibreOffice 프로필은 격리 안 `/tmp/lo-profile`이다. **잡마다** 변환 직전 같은 격리로 `/usr/bin/true`를 실행해 보고, 실패하면
  격리 없이 변환하지 않고 `unavailable`로 둔다.
- **일괄 재요청**: `enqueue_all_preview_jobs()`(037)가 변환 대상 판 중 변환본이 없는 판은 `pending`으로 만들고, `failed`·
  `unavailable` 판은 `pending`으로 되돌리며(pdf·error 비움), `pending` 판이 있는 문서마다 잡을 건다. `ready`는 건드리지 않는다.
  잠금 순서는 트리거·`fail_job`과 같다(문서 id 순). 돌려주는 값은 `pending` 판 수다. 운영자 CLI `openarchive rebuild-previews`가
  `services/system.py`의 `enqueue_preview_rebuild`를 거쳐 부르고, 기다리지 않는다.

**VM 실측(2026-10-09, Rocky 9.7 x86-64 에뮬레이션 VM·OpenSQL 17.8)**: rhwp 17쪽 179.8초(쪽당 약 10.6초) → 상한 900초.
실제 호스트에서 PID 네임스페이스 분리 확인(격리 안 프로세스 5개, 네트워크 `lo`만, `/home`·`/var` 안 보임). **재지 못한 것**:
구형 HWP의 외부 연결 그림(표본 없음 — 격리가 네트워크·파일을 막으므로 따라가도 닿을 곳은 없다), Firefox·Safari 내장 PDF 뷰어.

### 관계 생성 — 트리거가 잡을 만들고 워커가 판정한다

임베딩이 끝나 `embedding_status`가 `ready`로 **전이**하는 순간, AFTER 트리거가 **관계 잡** 한 행을 기록한다(`embedding_jobs`, `kind = 'edges'`). 판정은 워커가 그 잡을 집어 **자기 트랜잭션**에서 수행한다 — 임베딩을 커밋한 트랜잭션 안이 아니다 (ADR-029 결정 3 개정). 애플리케이션은 `document_edges`에도 `embedding_jobs`에도 직접 INSERT하지 않는다 — `document_versions`와 같은 원칙이며, 관계 잡도 트랜잭셔널 아웃박스와 코얼레싱을 그대로 쓴다.

```sql
-- 판정 본체는 일반 함수다. 부르는 곳은 워커의 관계 잡 하나다 — 전량 재계산(openarchive rebuild-edges)도
-- 잡을 걸 뿐이다 (014, 027)
CREATE FUNCTION rebuild_document_edges(target_document_id uuid) RETURNS void
  LANGUAGE plpgsql
  SET hnsw.ef_search = 200        -- 청크당 이웃 10 < ef_search (ADR-011 보강 4)
  SET random_page_cost = 1.1      -- ADR-011 보강 5
  SET enable_seqscan = off        -- 1만 청크 미만에서는 위 둘로도 플래너가 HNSW를 고르지 않는다 (#93 P1)
AS $$
BEGIN
  DELETE FROM document_edges WHERE src_document_id = target_document_id;   -- ★ 자기 src 행만
  -- 청크마다 다른 문서의 최근접 10개(청크별 상수 프로브 → HNSW)를 모은다
  --   → 문서쌍으로 접어 matched_src↓ · min_dist↑ · dst_document_id 순 5건까지 (MAX_NEIGHBOR_DOCUMENTS)
  --   → 양쪽 비율 ≥ 0.8 AND 양쪽 matched ≥ 3 이면 overlaps, 아니면 related
  --   → INSERT (src = 계산 주체 한 방향만)
  …
END; $$;

-- 트리거는 판정하지 않고 잡만 남긴다 (017). 문서당 pending 1건은 파셜 유니크 인덱스가 강제한다
--   uq_pending_job_per_doc_kind (document_id, kind) WHERE status = 'pending'
CREATE OR REPLACE FUNCTION build_document_edges() RETURNS trigger
  LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO embedding_jobs (document_id, kind) VALUES (NEW.id, 'edges')
    ON CONFLICT DO NOTHING;
  PERFORM pg_notify('embedding_jobs', NEW.id::text);   -- 최적화. 유실돼도 폴링이 집어간다
  RETURN NEW;
END; $$;

CREATE TRIGGER trg_build_document_edges                          -- (008) 정의는 그대로다
  AFTER UPDATE OF embedding_status ON documents
  FOR EACH ROW
  WHEN (NEW.embedding_status = 'ready' AND OLD.embedding_status IS DISTINCT FROM 'ready')
  EXECUTE FUNCTION build_document_edges();
```

- **저장은 단방향, 조회는 대칭이다.** `src_document_id`가 계산 주체이고 재계산은 자기 `src` 행만 교체한다. 양방향 두 행 + `DELETE both`는 남이 발견한 관계를 지워 재실행만으로 그래프가 흔들렸다(같은 규칙 재실행의 자카드 0.971 → 단방향 0.990). 읽는 쪽(검색 순회·관련 문서·태그 추천·군집·진단)이 `src ∪ dst`로 합친다 (ADR-029 개정).
- **세 설정은 함수 정의에 둔다.** 함수가 끝나면 호출 전 값으로 복원되어 관계 잡 트랜잭션의 나머지를 오염시키지 않는다. `SET LOCAL`로는 안 된다 — OpenProxy 풀 백엔드에 남는 PL/pgSQL generic plan이 이전 계획을 재사용해 `DISCARD PLANS` 뒤에야 먹었다. 실 VM 판정 비용은 10청크 4.6 s → 0.2 s, 159청크 40 s → 2.7 s다 (`OPENSQL_RESEARCH.md` §16). **이 시간이 임베딩 트랜잭션에서 빠진 것이 관계 잡 분리의 이유다.**
- **이웃 후보는 관계 잡이 처리되는 시점에 청크가 있는 문서뿐이다.** `ready` 전이 시점이 아니라 잡 처리 시점이므로, 큐가 밀리면 그 사이 적재된 문서도 후보에 들어와 대량 적재의 결과가 처리 순서에 따라 달라진다. 어느 쪽이든 먼저 들어온 문서가 나중 문서를 발견하는 것은 보장되지 않으므로, 대량 적재 뒤에는 `openarchive rebuild-edges`로 전체 기준으로 수렴시킨다 (ADR-029 결정 6). 이 명령은 판정을 직접 부르지 않고 DB 함수 `enqueue_all_edge_jobs()`로 `ready` 문서마다 관계 잡을 건 뒤 워커가 그 잡들을 비울 때까지 기다린다(027) — 모든 문서가 들어간 뒤의 잡은 전체 코퍼스를 보고 판정한다. 이미 대기 중인 관계 잡과는 `uq_pending_job_per_doc_kind`로 합쳐진다. `openarchive demo`(`openarchive/demo.py`)는 적재 끝에 이를 한 번 자동으로 한다.
- **판정이 실패해도 청크와 `ready`는 남는다.** 롤백 범위가 관계 잡 트랜잭션뿐이기 때문이다. 예외를 삼키지 않으며 워커의 재시도·백오프가 처리하고, 예산을 소진하면 **잡만** `error`로 격리된다 — `documents.embedding_status`는 건드리지 않는다(청크가 멀쩡해 검색이 그대로 되므로 임베딩 실패 배지를 붙이면 화면이 거짓말을 한다). 그 문서는 `/api/system/status`의 관계 미반영 수에 계속 세어진다.
- **관계 잡은 낡았다는 이유로 폐기하지 않는다.** 재임베딩이 시작된 문서라도 워커는 `documents`를 `FOR UPDATE`로 잠근 뒤 **지금 있는 청크로 판정하고** 마감한다. 임베딩 잡의 폐기 규칙(워커 루프 3번)을 옮겨 오지 않는 이유는 그 규칙의 전제 *"곧 올 `ready` 전이가 새 잡을 만든다"*가 재임베딩이 성공할 때만 참이기 때문이다 — `error`로 끝나면 `ready` 전이가 영영 오지 않아, 관계를 한 번도 계산하지 않은 문서가 남는데 잡은 `done`이라 관계 미반영 수는 0을 보고한다. 지키는 불변식은 **"`done`이 된 관계 잡은 반드시 판정을 돌렸다"**이며, 그래야 그 0이 참이다 (ADR-029 결정 3 개정).

### 워커 처리 루프

**기동**: 폴링 루프에 들어가기 전에 임베딩 모델을 한 번 **예열**한다 (ADR-003). `LocalProvider`는 첫 `embed()`까지 모델(~2GB) 로딩을 미루므로, 예열이 없으면 그 지연이 통째로 **첫 업로드**에 붙는다 — 가중치가 이미 캐시된 상태에서도 12~13초다(2026-08-21 실측). 아무도 기다리지 않는 기동 때 치른다. 예열 실패는 삼키고 루프를 계속한다: 최적화이지 새 실패 지점이 아니며, 모델을 못 받는 상황이라면 잡 처리의 기존 실패 경로가 `last_error`로 더 정확히 알린다.

이후 5초 주기 폴링이 **주 경로**. `LISTEN embedding_jobs` 수신은 폴링을 앞당기는 **최적화**이며, 동작하지 않아도 파이프라인은 정상 작동한다 (ADR-009).

**잡은 네 종류다** (`embedding_jobs.kind`). `embed`는 아래 2·3번의 청킹·임베딩·청크 교체이고, `edges`는 이미 저장된 청크 벡터로 관계만 다시 판정하며, `extract`는 최신 원본 판을 OCR해 문서 텍스트를 채우고, `preview`는 한글·오피스 원본 판을 PDF로 변환한다(위 「추출 잡」·「미리보기 변환 잡」). **큐·claim·백오프·좀비 회수·재시도 예산은 공유하고 처리 본체만 갈린다.** 관계 잡에 우선순위를 주지 않는 이유는 아래 1번에 있다.

1. 폴링 틱 또는 NOTIFY 수신 시 — 잡을 claim하고 **즉시 커밋**:
```sql
UPDATE embedding_jobs j
   SET status='processing', attempts=attempts+1, started_at=now(),
       lease_expires_at = now() + make_interval(secs => :job_lease_seconds)  -- 기본 60초
 WHERE j.id = (SELECT q.id FROM embedding_jobs q
                  JOIN documents d ON d.id = q.document_id
                WHERE q.status='pending' AND q.next_attempt_at <= now()
                ORDER BY (q.kind = 'preview'), q.id LIMIT 1   -- 미리보기 변환만 뒤로 (ADR-058)
                  FOR UPDATE OF q SKIP LOCKED
                  FOR NO KEY UPDATE OF d SKIP LOCKED)   -- 문서 행이 잠긴 잡은 건너뛴다 (#128)
RETURNING j.id, j.document_id, j.kind;

-- 임베딩 잡일 때만: 같은 트랜잭션에서 문서 상태도 processing으로 (UI 표시용)
-- 관계 잡·추출 잡은 여기서 documents를 건드리지 않는다 — 관계 잡은 임베딩이 이미 끝났고 ready가 맞다
UPDATE documents SET embedding_status='processing'
 WHERE id = %(document_id)s AND embedding_status <> 'processing';
```

   **미리보기 변환 잡만 뒤로 보내고, 나머지 종류는 가리지 않고 `id` 순으로 집는다.** 관계 잡에 우선순위를 주면 그 판정이 뒤에 오는 문서의 임베딩보다 먼저 돌아, 아직 청크가 없는 이웃을 못 보고 관계를 놓친다.

   **문서 행이 잠긴 잡도 건너뛴다** (#128). 임베딩 잡의 claim은 문서 행을 UPDATE하는데, 그 행을 죽은 OpenProxy 노드 너머의 고아 트랜잭션이 쥐고 있으면 기다리는 동안 워커가 서고, 상한을 걸어 실패시켜도 다음 주기에 같은 잡을 또 집어 뒤의 잡이 영영 오지 않는다. 문서 행을 잡 행과 함께 `SKIP LOCKED`로 잠가 두면 아래 UPDATE도 기다리지 않는다. 관계 잡도 종류를 가리지 않고 함께 건너뛴다 — 판정 트랜잭션이 그 문서 행을 먼저 잠그므로 집어 봐야 기다린다.

2. (`kind='embed'`) 문서의 최신 `content`와 **`content_hash`를 함께 읽기** → 청킹 → 임베딩
   *임베딩 잡의 DB 밖 연산이며, 추출 잡의 텍스트 추출·OCR도 DB 밖에서 실행한다.*
   `version`은 여기서 읽지 않는다 — 3번에서 잠금을 잡은 뒤 읽는다 (아래 설명).

3. (`kind='embed'`) **단일 트랜잭션**으로 결과 반영 — 단, **읽었던 `content_hash`를 재확인**한다:
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

  UPDATE documents SET embedding_status='ready' WHERE id = %(doc_id)s;   -- ← 여기서 트리거가 관계 잡을 남긴다
  UPDATE embedding_jobs SET status='done', finished_at=clock_timestamp() WHERE id = %(job_id)s;
COMMIT;
```

> **`document_chunks.version`은 반드시 3번의 `FOR UPDATE` 아래에서 읽은 값으로 채운다.**
> 이 컬럼은 정합성 검증 쿼리(`c.version <> d.version`)와 `/admin/status` 카운터의 근거다. **잘못 채우면 카운터가 영원히 0이거나 영원히 0이 아니게 되어 지표 자체가 무의미해진다.**
>
> 2번에서 미리 읽지 않는 이유: 본문이 `A → B → A`로 되돌아온 경우 `content_hash`는 원래대로 돌아오지만 `version`은 2 올라가 있다. 해시 재확인은 통과하는데 2번에서 읽은 `version`은 낡은 값이 된다. 잠금 아래에서 읽으면 이 경우에도 잠금 시점의 `version`이 정확히 기록된다.

> **`finished_at`은 `now()`가 아니라 `clock_timestamp()`다.** `now()`는 트랜잭션 시작 시각이라, 같은 트랜잭션 안에서 도는 관계 생성 트리거의 시간이 잡 소요에서 통째로 빠졌다 — #93에서 잡 시간으로 트리거 비용을 추정하다 10배 넘게 틀렸다. 관계 판정이 잡으로 분리된 지금(016·017) 임베딩 잡에는 그 왜곡 자체가 없지만, **관계 잡의 마감도 같은 이유로 `clock_timestamp()`를 쓴다** — 판정 시간이 그 잡의 소요에 들어가야 한다. `fail_job`·마감 경로의 `finished_at`도 같다.

   **관계 잡(`kind='edges'`)은 2·3번을 타지 않는다.** 본문을 읽지도, 임베딩 모델을 부르지도 않는다. 자기 트랜잭션에서 `documents`를 `FOR NO KEY UPDATE`로 잠그고 — 판정 도중 청크가 교체되면 방금 계산한 관계가 사라진 청크 번호를 가리킨다 — `SELECT rebuild_document_edges(...)` 한 줄을 부르고 잡을 `done`으로 마감한다. 워커는 판정 규칙을 복제하지 않는다: 판정은 DB 함수 하나이고, 그 함수를 부르는 곳도 관계 잡 하나다 — `openarchive rebuild-edges`의 전량 재계산은 잡을 걸 뿐이다(027, #156). 판정이 성공하면 같은 문서의 `error`로 격리된 관계 잡을 함께 마감한다 — 판정은 문서의 관계를 통째로 교체하므로 격리된 잡이 요구한 일을 했다. 함수는 첫 문장에서 같은 문서 행을 `FOR NO KEY UPDATE`로 잠가, 한 문서의 재계산이 둘 겹치면(워커 여럿) 차례로 돈다(024) — 전량 재계산이 잡을 거치지 않던 때는 문서를 잠그지 않아 워커와 겹치면 교착했다. `FOR UPDATE`가 아닌 이유는 이 문서를 가리키는 관계 INSERT의 FK 검사(`FOR KEY SHARE`)와 충돌하지 않게 하려는 것이다. 서로의 이웃인 두 문서를 동시에 계산해도 교착하지 않는다. 판정을 건너뛰는 경우는 **문서가 이미 삭제됐을 때 하나뿐이다**(위 「관계 생성」 절).

   **추출 잡(`kind='extract'`)도 2·3번을 타지 않는다.** 최신 원본 판을 OCR해 문서 텍스트를 쓰는 데서 끝나고, 그 UPDATE가 발화시킨 새 `embed` 잡이 2·3번을 탄다(위 「추출 잡」 절). 인식 결과가 비었거나 너무 커도 잡은 `done`으로 마감하고 문서만 `failed`로 표시한다.

4. 실패 시: `attempts` 기반 지수 백오프로 `next_attempt_at` 갱신 후 `pending` 복귀. `attempts`는 claim 시점에 이미 올라 있으므로 **3회를 소진하면**(3회째 실패) job `error` + `documents.embedding_status='error'`. **문서를 `error`로 떨어뜨리는 것은 임베딩 잡뿐이다** — 관계 잡이 소진되면 잡만 `error`로 남고, 추출 잡이 소진되면 `embedding_status`가 아니라 `extraction_status='failed'`가 되며, 미리보기 잡이 소진되면 그 문서의 `pending` 변환본만 `failed`가 된다.

5. 좀비 회수: **lease가 만료된** `processing` 잡을 `pending`으로 리셋한다 (ADR-050). `claim_job`이 `lease_expires_at = now() + JOB_LEASE_SECONDS`(기본 60초)를 찍고, 워커는 처리하는 동안 **처리 연결과 다른 연결**로 lease의 1/3(20초)마다 연장한다. 연장은 `WHERE id = … AND status = 'processing' AND attempts = …`라 이미 회수된 잡을 되살리지도, 회수 뒤 다른 워커가 다시 집은 잡의 lease를 대신 늘리지도 못한다 — 선점마다 `attempts`가 오르고 스윕은 그것을 건드리지 않으므로 `(id, attempts)`가 한 번의 선점을 가리킨다. 워커가 죽든, 워커는 살아 있는데 연결이 끊기든 연장이 멈추므로 lease 뒤에 회수된다 — 판정 기준이 "얼마나 오래 걸렸나"가 아니라 "소유자가 아직 살아 있나"라서, 정상적으로 오래 걸리는 잡은 회수되지 않는다. `processing`인데 lease가 없는 행은 스윕이 영원히 회수하지 못하므로 제약(`embedding_jobs_processing_has_lease`, 020)이 막는다.

   `sweep_zombies()`는 워커 신원으로 거르지 않는 **전역 스윕**이라 다른 워커가 남긴 좀비도 회수한다. 스윕은 워커 **루프 머리**에 있어 첫 반복이 곧 기동 시 1회 스윕이고, **drain 중에도 잡과 잡 사이에서 lease 주기로** 돈다. 머리에서만 돌면 drain이 끝날 때까지 회수가 밀린다(#110 B의 S2-4는 약 13분).

   **잡을 잃은 워커는 결과를 쓰지 않는다.** 연장이 0행이거나 lease 동안 한 번도 성공하지 못하면 잃은 것으로 보고, 임베딩 뒤·반영 전에 포기한다 — 청크를 쓰지도, 잡을 마감하거나 실패로 기록하지도 않는다. 잡은 이제 되찾아 간 쪽의 것이다. 연결 오류 한 번으로는 포기하지 않는다(lease가 주기의 세 배다). 처리 중인 태스크를 취소하지 않는 것은 트랜잭션 중간에 끊긴 연결이 풀을 오염시키기 때문이다(#110 B-2) — heartbeat도 같은 이유로 취소하지 않고 종료 신호로 멈춘다. 다만 워커가 잃었다고 알아채는 것은 늦을 수 있다(연장 주기 사이, 풀 대여 대기, 확인과 반영 사이). 그래서 이 포기는 헛일을 줄이는 최적화이고, **남의 잡에 쓰지 않는 보장은 DB가 쓰는 순간 한다** — 결과 반영·관계 판정·실패 기록 트랜잭션은 문서 행을 잠근 뒤 잡을 `(id, attempts)`와 `processing`으로 다시 잠가 확인하고(`lock_owned_job`), 아니면 아무것도 쓰지 않는다. 문서 → 잡 잠금 순서가 스윕과 같아, 확인한 뒤 커밋까지 스윕이 끼어들지 못한다.

   **어떤 락도 워커를 끝없이 세우지 못한다** (#128, #122 S5a-2). 죽은 OpenProxy 노드를 거치던 트랜잭션은 Primary가 끊을 때까지(서버 keepalive 기본 2시간) 문서·잡 행 락을 쥔 채 남는다. S5a-2에서 lease 연장이 그 락을 761초 넘게 기다렸고, 워커는 heartbeat 종료를 상한 없이 기다려 13분 넘게 처리 0이었다. 서버 설정(ADR-051)이 근본 해결이고, 앱은 그 설정이 없는 DB에서도 버티도록 셋을 둔다.
   - **일감을 고르는 쿼리는 기다리지 않는다.** claim(1번)과 스윕은 잠긴 문서·잡을 `SKIP LOCKED`로 건너뛴다. 스윕이 서거나 실패하면 루프 머리의 drain도 돌지 않기 때문이다. 건너뛴 좀비는 막은 쪽이 풀린 뒤의 스윕이 회수한다.
   - **자기 잡에 쓰는 트랜잭션은 한 heartbeat 주기(lease의 1/3)까지만 기다린다.** lease 연장·결과 반영·관계 판정·실패 기록·반납이 첫 문장으로 `set_config('lock_timeout', …, true)`를 건다(OpenProxy transaction 모드라 트랜잭션 밖 SET은 못 쓴다). 상한에 걸리면 `LockNotAvailable`로 끝나고, 잡은 lease 만료 뒤 스윕이 회수한다. 반영이 상한에 걸리면 이어지는 실패 기록도 같은 문서 행 락에 걸리므로, 예외가 루프까지 올라가 그 주기의 drain을 접고 연결을 버린 뒤 다음 주기로 넘어간다.
   - **heartbeat는 최대 한 lease만 기다린다.** 넘기면 취소하지 않고 떼어 둔다 — 취소는 풀을 오염시키고(#110 B-2), 쿼리가 도는 연결을 밖에서 닫는 것은 안전하지 않으며, OpenProxy 너머의 쿼리 취소는 실패했다. 떼어 둔 heartbeat는 진행 중인 호출이 끝나면 멈추고, 오류로 끝난 연결은 `openarchive.db.connection`이 버린다. 그때까지 풀 연결 하나를 쥐며(락이면 한 주기, 응답 없는 네트워크면 클라이언트 keepalive 약 60초), 쌓여서 풀이 차면 다음 heartbeat가 연장하지 못해 그 잡의 결과를 버린다(ADR-050 트레이드오프 6).

   **회수에도 4번과 같은 재시도 예산이 걸린다.** lease가 만료된 잡의 `attempts`가 이미 3회를 소진했으면 `pending`으로 되돌리지 않고 job `error` + `documents.embedding_status='error'`로 격리한다(`last_error`는 `WorkerCrashLoop: …`). 여기서도 `embedding_status`를 건드리는 것은 임베딩 잡뿐이며(추출 잡은 `extraction_status='failed'`), 회수·격리 판정은 종류를 가리지 않고 `(document_id, kind)` 단위로 이뤄진다. 이것이 없으면 상한이 4번(예외로 잡히는 실패)에만 걸린다 — `claim_job`은 `attempts`를 보지 않으므로, 워커 프로세스를 죽이는 잡은 회수 → 재선점 → 재크래시를 무한 반복하고 그때마다 워커가 함께 죽는다. `attempts`를 초기화하지 않는 것은 이 판정의 전제이지 그 자체로 상한을 만들지는 않는다.

   격리해도 청크는 지우지 않는다. 검색은 이전 버전으로 계속되고 정합성 카운터는 어긋난 채 남는다 — 격리했다고 어긋남을 숨기면 계약이 거짓말이 된다. 재개 수단은 문서 재수정이며, 본문을 바꾸지 않고 다시 태우려면 `UPDATE documents SET content_hash = content_hash`를 쓴다(003_triggers.sql).

   남는 한계는 상태 표시다. 워커가 죽어 있는 동안 `/api/system/status`의 `jobs.processing`은 방치된 잡을 계속 세므로, **처리 중이 아닌 잡이 처리 중으로 보인다.** lease가 지나면 회수되므로 데이터 문제는 아니지만, 그 구간에는 화면이 사실과 다르다. `recovery_pending`(lease가 만료된 `processing`)이 그 구간을 따로 세므로 구분은 가능하다.

   **휴지통 비우기도 루프 머리에서 한다** (ADR-060). 스윕·멱등키 정리 다음에 `services/trash.py`의 `purge_expired`가 `deleted_at`이 `TRASH_RETENTION_DAYS`(기본 30일)보다 오래된 문서를 영구 삭제한다 — 잡 종류를 늘리지 않고, 행위자는 `actor_via='worker'`로 감사에 `document_deleted`가 남는다. 휴지통 문서의 잡은 다른 문서처럼 처리한다.

6. 정상 종료: `SIGTERM`(배포의 `systemctl stop`)·`SIGINT`(Ctrl-C)를 받으면 **처리 중인 잡을 마치고** 루프를 빠져나온다. 임베딩을 시작하기 전이었다면 선점을 반납한다 — `pending` 복귀 + **`attempts` 원복**. 배포로 워커를 세우는 것은 잡의 실패가 아니므로 예산을 소비하면 안 된다. 원복하지 않으면 배포를 3회 반복하는 것만으로 멀쩡한 문서가 5번의 소진 판정에 걸려 `error`가 된다. 반납은 백오프를 걸지 않아 다음 워커가 곧바로 이어받는다.

   반납하지 못하고 죽어도(SIGKILL·OOM) 정합성은 깨지지 않는다. 잡이 좀비로 남아 5번이 lease 만료 후에 회수할 뿐이다. 반납은 그 대기를 없애는 최적화다.

7. 프로세스 감독: 워커가 `SIGKILL`·OOM으로 사라지면 스스로 살아날 수 없고, **되살리지 않으면 임베딩 파이프라인이 통째로 멈춘다** — 새 문서는 영원히 검색되지 않고 수정된 문서는 옛 벡터로 검색된다. 워커는 보통 `openarchive serve`가 API와 함께 띄운다. serve가 묶는 것은 **기동과 종료뿐**이다 — 한쪽이 멈추면 나머지도 내리고 0이 아닌 코드로 끝나 반쪽만 도는 상태(업로드는 되는데 검색에 안 잡히는 상태)를 만들지 않지만, 죽은 프로세스를 되살리지는 않는다. 되살리는 것은 serve 바깥 감독자(systemd 등)의 일이다 (ADR-038·039). `scripts/deploy_app_host.sh`로 배포하던 호스트에서는 워커만 systemd **user** 유닛(`scripts/openarchive-worker.service` → `~/.config/systemd/user/`, `Restart=always`)으로 따로 돌렸다. system 유닛이 아닌 이유는 SELinux다 — Enforcing에서 `init_t`가 홈 아래 venv를 실행하지 못한다 ([ADR-038](ADR.md)).

   **재기동 직후 좀비가 즉시 회수되지는 않는다.** 소유자의 죽음을 lease 만료로 판정하므로 lease(기본 60초)를 기다린다. 그 대기가 파이프라인 전체를 멈추지는 않는다 — 좀비는 `processing`이라 `claim_job`의 대상이 아니고, 워커는 남은 `pending` 잡을 그대로 집어간다. 크래시의 영향은 그 잡 하나로 격리된다(`test_pipeline_keeps_draining_while_a_zombie_waits_for_its_lease`).

> **4번과 5번에는 공통 예외가 있다: 그 문서에 같은 종류(`kind`)의 새 `pending` 잡이 이미 있으면 `pending`으로 되돌리지 않고 `done`으로 마감한다.** 6번의 반납도 같다.
>
> 처리 중 문서가 수정되면 트리거가 새 잡을 만든다. 이때 실패한 잡이나 좀비 잡까지 `pending`으로 되돌리면 **문서·종류당 pending 1건**을 강제하는 `uq_pending_job_per_doc_kind` 위반이 되어 **워커가 죽는다.** 종류가 인덱스 키에 들어간 덕에 같은 문서의 임베딩 잡과 관계 잡은 서로를 밀어내지 않는다 — 재임베딩 중에 만들어진 관계 잡이 코얼레싱에 사라지면 안 되기 때문이다. 코얼레싱 제약과 재시도 로직이 만나는 지점이며, 파셜 유니크 인덱스를 둔 이상 구조적으로 발생한다.
>
> 확인과 복귀 사이의 경쟁을 없애려면 **문서 행을 `FOR UPDATE`로 잠근 뒤** 판단한다. 잡 생성은 전부 문서 변경 트리거 안에서 일어나므로 이 잠금이 새 잡의 커밋을 막는다. 잠그지 않으면 READ COMMITTED의 statement 스냅샷 탓에 방금 커밋된 새 잡을 놓치고, 그 `pending` 복귀가 유니크 제약 위반으로 터진다. 마감해도 유실이 아니다 — 최신 내용은 새 잡이 처리하며, 이는 3번에서 낡은 결과를 폐기하면서도 잡을 `done`으로 마감하는 것과 같은 원리다.
>
> **이 예외는 4번과 5번의 재시도 소진 판정보다 먼저 본다.** 낡은 내용을 보던 잡의 수명이 끝난 것이지 문서가 실패한 것이 아니므로, 소진 시점이라도 `documents.embedding_status`를 `error`로 떨어뜨리지 않는다. 순서를 뒤집으면 새 잡이 곧 `ready`로 되돌릴 문서에 거짓 `error` 배지가 뜬다.

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
| **관계 반영** | 관계 판정은 임베딩과 **다른 트랜잭션**에서 돈다 — `ready` 전이가 관계 잡을 남기고(같은 아웃박스·같은 코얼레싱) 워커가 처리한다. 판정이 실패해도 청크와 `ready`는 남으며, 임베딩 완료와 관계 반영 사이의 구간은 `/api/system/status`의 관계 미반영 문서 수로 **관측한다** (ADR-029 결정 3 개정) |
| 멱등성 | 청크 교체가 delete+insert라 잡 재실행의 종착 상태가 항상 동일 |

> **이 표는 즉시 반영을 보장하지 않는다.** 재임베딩 중에는 이전 버전이 검색되고, 폴링 주기(5초)와 임베딩 소요만큼 반영이 늦고, **관계는 임베딩이 끝난 뒤 별도 잡으로 계산되므로 그만큼 더 늦으며**, Failover 구간에는 요청이 실패한다. 우리가 보장하는 것은 **버전 일관성**과 **최신으로의 수렴**이며, 그 사이의 어긋난 구간은 정합성 검증 쿼리로 **관측할 수 있다**. 사용자 대상 문구에서 쓰지 않을 표현은 **ADR-015가 문자 그대로 열거한다** — 이 문서에서 되풀이하지 않으니 그쪽을 근거로 삼는다.

## 고가용성(HA) 전략

### OpenSQL 클러스터 구성 (공식 권장 3노드)

```
                    ┌──────────────────────────────┐
   애플리케이션 ───▶│  VRRP VIP  (OpenProxy)       │  ← 단일 엔드포인트
   (API · 워커)     └──────────┬───────────────────┘
                               │ [general.etcd] → Patroni leader 키 watch
                               │ 쓰기·BEGIN → primary / 트랜잭션 밖 SELECT → replica
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
| Primary 변경 감지 후 재연결, VIP 이중화 | **OpenProxy** (etcd 연동 + VRRP VIP — 3노드에서 실측, 아래 참조) |
| 커밋 응답을 받은 쓰기의 failover 생존 | **OpenHA Cluster Manager (Patroni)** — 비동기 복제, 뒤처진 replica 승격 제외(`maximum_lag_on_failover` 1MB) (ADR-049 개정) |
| 미처리 잡의 무손실 보존 | **DB 계층** (`embedding_jobs`는 WAL 로깅 테이블 → 스탠바이 복제) |
| 연결 끊김 시 재시도, 잡 재개, 좀비 회수 | **애플리케이션 (우리)** |
| 죽은 연결 감지·오염 연결 폐기, 일시 불가용의 503·백오프, 문서 생성 멱등키, 잡 lease | **애플리케이션 (우리)** (ADR-047·048·050) |
| 백업·시점 복원(PITR) — 잘못 지운 데이터·손상·클러스터 전체 상실 | **Barman** (전용 노드 node4, WAL streaming + 영구 슬롯, ADR-053). 복원은 운영자가 격리 인스턴스로 한다. 앱에는 휴지통이 없다 — 삭제는 즉시 영구이고 되살리는 경로는 이 복원뿐이다 |

> **`PROJECT_CONTEXT.md` 설계 원칙 준수**: 위 표에서 주체가 OpenSQL 컴포넌트인 줄은 OpenSQL이 제공하는 기능이므로 애플리케이션에서 중복 구현하지 않는다 (ADR-006).
>
> **5번째 줄은 Single에서는 구성되지 않았고, 3노드에서 실측했다.** Single 설치의 `openproxy.toml`에는 `use_patroni`도 `[general.etcd]`도 없고 `servers`에 primary 하나가 하드코딩돼 있어 OpenProxy는 **정적 서버 목록을 가진 순수 커넥션 풀러**였다 (ADR-006 실측 정정). 2차의 3노드에서는 **설치기 출력도 공식 HA 구성이 아니어서** etcd 연동·`transaction`·읽기/쓰기 분리·VIP를 손으로 교정했고(`SETUP_OPENSQL.md` §16), 그 구성에서 switchover 쓰기 중단 10.3초·VIP 이동 8.2초를 쟀고(ADR-020 2026-09-28 개정), 비동기로 되돌린 최종 구성에서는 각각 10.1~10.3초·10.2초였다(#165).

### 애플리케이션 접속

```bash
# 단일 엔드포인트. 멀티호스트 DSN·target_session_attrs 사용 안 함.
DATABASE_URL="postgresql://app@<vip>:6432/<pool_name>"
```

- `<pool_name>`은 `openproxy.toml`의 `[pools.<name>]` 이름이다 (DB 이름 자리에 pool 이름을 넣는 것이 OpenProxy 규약).
- 로컬 개발 시에는 같은 환경변수에 단일 컨테이너 주소를 넣는다. **코드는 로컬/클러스터를 구분하지 않는다.**

### 애플리케이션이 담당하는 복구 로직

- **API**: `psycopg_pool.AsyncConnectionPool(check=AsyncConnectionPool.check_connection)` — 죽은 연결을 대여 시점에 감지·폐기·재수립. 처리 도중 끊긴 요청은 미들웨어가 **1회 재시도**하되 대상은 **읽기 전용 요청**뿐이다(`GET`·`HEAD`·`POST /api/search`·`POST /api/ask` — ask는 DB 단계가 생성 전에 끝나 재시도가 생성을 두 번 돌리지 않는다). 쓰기는 커밋 도달 여부를 구분할 수 없어 재시도 시 중복 생성 위험이 있다 (ADR-023).
- **일시 불가용은 503 + `Retry-After`**: 기다리면 풀리는 DB 오류를 `openarchive.db.is_unavailable` 하나로 가른다 — 연결 유실·풀 대여 시간 초과(SQLSTATE 없는 `OperationalError`), 연결 예외 `08xxx`, OpenProxy `AllServersDown`과 서버 소켓 오류가 올라오는 `58000`, 서버 종료·기동 중인 `57P01`·`57P02`·`57P03`, 승격 직후 쓰기가 replica로 간 `25006`. **나열한 것만** 일시 불가용이다 — `OperationalError`에는 디스크 가득 참(`53100`)·인증 실패(`28P01`)·statement timeout(`57014`)처럼 기다려도 풀리지 않는 것도 섞여 있어, 그것을 503으로 주면 결함이 가려진다. 한계: 잘못된 DSN·비밀번호는 풀에서 `PoolTimeout`으로 보여 장애와 구별되지 않는다. 미들웨어(`api/retry.py`)의 즉시 1회 재시도도 이 기준을 따르고, 끝내 풀리지 않으면 **503 + `Retry-After: 1`**로 응답한다. 쓰기도 503은 받지만 즉시 재시도는 `Idempotency-Key`가 있는 문서 생성(`POST /api/documents`·`/api/documents/text`)만 한다 — 다른 쓰기는 헤더가 붙어 와도 키를 지키지 않는다. 그 밖의 오류는 500이며, 500은 코드 결함에만 남는다. #110 B에서는 장애 구간 응답이 전부 500이었다(B-5) (ADR-048 결정 3).
- **긴 재시도는 클라이언트가 한다**: 즉시 1회로는 7~42초 중단을 덮지 못한다. 웹 UI(`lib/api.ts`)의 읽기와 업로드, MCP 도구 4개(읽기 3개와 `create_document`)가 503·네트워크 오류(MCP는 분류된 DB 오류)를 **지수 백오프(1초 시작·상한 8초) + 전체 지터, 총 60초**로 다시 시도한다. 웹 UI는 503의 `Retry-After`보다 일찍 보내지 않는다 — 간격은 `max(Retry-After, 지터 백오프)`이고, 알린 값이 남은 예산을 넘으면 바로 포기한다(RFC 9110 §10.2.3). stdio·원격 MCP 도구는 REST를 거치지 않고 서비스를 직접 부르며 같은 `with_backoff`를 쓴다. 도구 내부의 DB 실패는 MCP 오류로 전달된다. DB 불가용을 HTTP 503으로 받는 것은 토큰 인증 단계뿐이며 `RetryOnUnavailable`이 변환한다(매니저 미기동도 별도로 503). 쓰기는 멱등키가 있는 것만 재시도한다 — 웹 UI는 업로드 동작마다, MCP는 `create_document` 호출마다 키를 하나 만들어 그 요청의 모든 재시도에 쓴다(ADR-047). 편집·태그·삭제 등 다른 쓰기는 재시도하지 않는다. REST 서버는 긴 백오프 동안 요청을 붙잡지 않는다. 원격 MCP는 예외로 공유 도구 본체가 서버 안에서 백오프하므로 장애 구간의 도구 응답은 그동안 기다린다 — MCP 도구 호출에는 클라이언트가 멱등키를 실을 자리가 없어 같은 키로 재시도할 수 있는 곳이 서버뿐이고, 대기는 연결을 반납한 뒤라 풀을 묶지 않는다 (ADR-048 결정 4·「2026-10-07 개정」, ADR-056 구현 결정 7).
- **워커 (잡 처리)**: 동일한 풀 정책. 처리 중 연결이 끊기면 트랜잭션이 롤백되고, 잡은 `processing` 상태로 남았다가 좀비 회수 스윕이 `pending`으로 되돌린다.
- **죽은 연결 감지 (keepalive)**: 풀과 워커 `LISTEN` 연결은 TCP keepalive(`keepalives_idle=30`·`interval=10`·`count=3`)와 `tcp_user_timeout=60000`을 **코드 기본값**으로 연다(`openarchive/db.py`). VIP가 원래 노드로 돌아가는 순간(선점) 응답을 기다리던 연결은 FIN도 RST도 받지 못하는데, OS 기본값으로는 약 2시간 뒤에야 풀려 워커가 멈춰 있었다(#110 B-1). 감지는 약 60초 안에 된다. DSN에 같은 키를 적으면 그 값이 이기며, DSN 문자열 자체는 바꾸지 않는다 — 환경변수 하나·호스트 하나(ADR-006) 그대로다 (ADR-048 결정 1).
- **오류가 난 연결은 풀에 돌려보내지 않는다**: API 요청·MCP 도구 호출이 DB 오류로 끝나면(`openarchive.db.connection`), 워커 처리 루프는 어떤 오류로든 끝나면 그 연결을 닫아 풀이 버리게 한다. OpenProxy가 `BEGIN`에 `AllServersDown`을 돌려주면 psycopg의 `transaction()` 카운터가 되돌려지지 않은 채 연결이 IDLE로 남고, 풀은 IDLE만 보고 받아들여 그 연결의 다음 `transaction()`마다 `AssertionError`가 났다(#110 B-2, 한때 워커 풀 4개 중 3개). 요청 경로는 HTTP 거절(401·404)로는 닫지 않는다. 워커는 좁히지 않는다 — 잡 처리 중 오염되면 `process_once`가 첫 DB 오류를 잡아 `fail_job`으로 넘기고, 루프에 올라오는 것은 `fail_job`의 `AssertionError`다 (ADR-048 결정 2).
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

**② etcd 정지 — 실측 문서만 유지 (Single 설치 2026-08-09 기준).** etcd를 99초 정지했을 때 `failsafe_mode=true`가 primary 강등을 막아 앱은 전 구간 아무것도 눈치채지 못했고 6432·5432 쓰기가 계속 가능했다. 즉 DCS 장애는 곧 서비스 장애가 아니다.

**③ Patroni 정지 — 실측 문서만 유지 (Single 설치 2026-08-09 기준).** Patroni만 `SIGKILL`해도 PostgreSQL은 계속 쓰기를 받았지만, etcd의 리더 키는 23.9초에 소멸했고 106초 관측 동안 아무것도 Patroni를 되살리지 않았다. 이 설치의 systemd 유닛이 `opensql-etcd.service` 하나뿐이고 Patroni·PostgreSQL·OpenProxy는 `nohup` 맨 프로세스라는 구성과 일치한다. HA 3노드는 Patroni·OpenProxy도 systemd로 등록해 재부팅 뒤 자동 재합류한다(`SETUP_OPENSQL.md` §16 교정 4).

②·③은 시연 시간에 비해 핵심 서사를 분산시키므로 코드로 만들지 않았다. 실측 조건과 타임라인은 `OPENSQL_RESEARCH.md` §0 「Single 장애 주입 실측」에 남긴다.

**④ 3노드 장애 주입 — 실행 코드 (#110·#122).** `scripts/ha_failover.py`는 앱 API·워커를 VIP에 붙인 채 업로드·검색·쓰기 probe 부하를 걸고 장애 명령을 실행한 뒤, 장부(커밋 응답을 받은 업로드의 존재·sha256, 실패 응답인데 남은 행, 중복)·사용자 가시 실패·원시 500·정합성 카운터 수렴·3노드 다이제스트·승격 대상을 자동 판정한다. 업로드는 텍스트와 파일을 번갈아 보내 원본 판(`document_files`)의 sha256·판 번호와 문서 버전까지 대조한다(#165). 사용법과 회차별 결과는 `SETUP_OPENSQL.md` §16 「최종 구성 장애 검증」(지금 구성)과 「장애 주입 측정」(동기 복제 시절). Primary 프로세스·노드 사망·Replica 사망·VIP MASTER 사망·Leader 재부팅·Leader 네트워크 분리·etcd 1대·과반 상실·switchover 전부에서 **앱 무재시작 · 유실 0 · 사용자 가시 실패 0 · 카운터 0 수렴**이었다.

**공식 3노드 구성(비동기 복제)에서 Primary 프로세스·노드 사망, switchover, VIP 노드 사망, Leader 재부팅·네트워크 분리, etcd 1대·과반 상실을 부하 중에 주입해, 앱을 재시작하지 않고 수십 초 안에 자동 복구됨을 실측했다. 커밋 응답을 받은 업로드(원본 파일 포함)의 유실·중복은 측정한 모든 회차에서 0이었고, 정합성 카운터는 매번 0으로 수렴했다. 비동기 복제이므로 이는 관측값이지 RPO 0 보장이 아니다 — failover 순간 replica에 닿지 않은 커밋은 잃을 수 있다. 복구 구간에는 쓰기가 멈추며, 클라이언트는 그 구간을 백오프 재시도로 넘긴다.** (ADR-020 결정 4, 2026-10-04 개정)

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
| `synchronous_mode` | **끔(템플릿 그대로, 비동기)** — 9/27 동기 1대로 켰다가 9/30 되돌림. OpenProxy 1.1.3이 `sync_standby` 역할을 몰라 기동하지 못한다 (ADR-049 개정) |

3노드 실측 쓰기 중단(부하 중, 최종 구성 #165): Primary 노드 사망 30.6~40.8초 · Leader 네트워크 분리 30.7초 · postmaster kill 14.1초 · switchover 10.1~10.3초 · VIP 이동 10.2초 · Leader 재부팅 10.3초 · Replica·etcd 1대·etcd 과반 상실 0초. 상세는 `SETUP_OPENSQL.md` §16 「최종 구성 장애 검증」, 동기 복제 시절 수치(#122)는 `OPENSQL_RESEARCH.md` §3 「3노드 실측」.

> **정확한 표현은 "짧은 중단 후 자동 복구"다.** 장애 감지부터 승격까지 수십 초가 걸린다(위 실측). 그 구간의 쓰기는 멈추고, 서버는 503을 돌려주며 클라이언트가 백오프로 넘긴다(ADR-048). 이 자리에서 쓰지 말아야 할 반대말은 **ADR-015와 ADR-020 결정 4가 문자 그대로 지정한다** — 이 문서에서 되풀이하지 않는다.

### 제약: `max_connections = 100`

OpenSQL `patroni.yml`의 PostgreSQL 파라미터는 `max_connections: 100`이다. API 풀 + 워커 잡 처리 풀 + 워커 LISTEN 연결이 모두 이 안에 들어가야 하며, OpenProxy의 `pool_size`와 함께 산정한다.

### 로컬 개발 갭

로컬은 pgvector 단일 컨테이너다 (ADR-007). **OpenProxy 경유 경로는 로컬에서 검증할 수 없다** — 공식 Docker 배포판이 없기 때문이다. M0 검증 목록은 `docs/OPENSQL_RESEARCH.md` §12를 따른다.

## API 설계

문서 목록은 `services/documents.py`의 `list_documents`가 열람 술어와 상태·유형·태그 필터,
제목 부분 일치(`ILIKE`)를 한 SQL에 적용한다. 제목 검색어는 앞뒤 공백을 제거하고 `\`·`%`·`_`를
이스케이프해 와일드카드가 아닌 문자로 찾는다. 기본 정렬은 `updated_at DESC, id`이며,
`sort=title`은 `title, id` 순이다. `count_documents`는 목록과 같은 WHERE 조각과 필터 값을
사용해 페이지 제한 없이 별도 SQL로 센다 — 조건 건수에도 열람 범위가 적용된다.
`list_visible_tags`는 열람 가능한 문서의 태그만 중복 없이 태그순으로 반환한다.

| 엔드포인트 | 내용 |
|---|---|
| `POST /api/documents` | multipart 업로드. 형식별 파서로 추출 → INSERT. 여기서 트리거가 파이프라인을 자동 기동 — 임베딩 관련 코드 없음. **이미지와 텍스트 레이어가 빈 쪽·글자 정보가 깨진 쪽이 있는 PDF는 추출하지 않고 `extraction_status='pending'`으로 INSERT해 OCR을 워커 잡에 넘긴다** (「추출 잡」). 그 밖의 형식에서 **텍스트 추출 결과가 비면 400** (아래). **원본 파일은 같은 트랜잭션에서 `document_files`에 1판으로 저장한다** — 원본 저장이 실패하면 문서·텍스트 버전·잡도 남지 않는다. 상한 `MAX_UPLOAD_MB`(기본 50) 초과는 413. 선택 헤더 `Idempotency-Key`(아래) |
| `POST /api/documents/text` | JSON 텍스트 공급(`txt`·`md`). 선택 `folder_id`는 업로드 Form과 같다(아래 「폴더」). `filename`은 NULL이며, 파생 데이터는 업로드 경로와 동일하게 DB 트리거가 만든다. 빈 문서 텍스트와 500,000자 초과는 400. 선택 헤더 `Idempotency-Key`(아래) |
| `GET /api/documents` | 목록 + `status`(임베딩 상태)/`extraction_status`/`tag`/`q`(제목 부분 일치)/`content_type` 필터. `folder_id`(그 폴더에 **직접** 든 문서만, 하위 폴더 제외) 필터. `sort=updated`(기본, 최근 수정순) 또는 `title`(제목순). embedding_status·extraction_status 포함. 요약·상세에는 문서 자신의 `visibility`와 함께 실제로 적용되는 공개범위 `effective_visibility`(「폴더 범위 따름」이면 최상위 폴더의 값 — 폴더로 만든 문서의 자기 범위는 `private`로 닫혀 있다)를 싣는다. 인식 실패 문서는 `status=pending`에 남으므로 곧 임베딩될 문서는 `extraction_status=done`을 함께 준다 (ADR-052 결정 4 보강) |
| `GET /api/documents?limit=&offset=` | 같은 목록의 한 페이지(`limit` 1~100). 빼면 전부 — MCP·export는 전체를 본다. 첫 화면은 50건씩 쓴다 (#95-d) |
| `GET /api/documents/count` | 목록과 같은 `status`·`extraction_status`·`tag`·`q`·`content_type`·`folder_id` 조건의 전체 건수 `{total}`. 화면은 이 건수로 페이지를 나눈다 |
| `GET /api/documents/tags` | 열람 가능한 문서의 태그 목록 `string[]`. 중복 없이 태그순이며 보이지 않는 문서의 태그는 포함하지 않는다 |
| `GET /api/documents/progress` | 열람 범위 안 문서의 파이프라인 단계별 수(`extracting`·`extraction_failed`·`pending`·`processing`·`ready`·`error`). 인식이 끝난 문서만 임베딩 단계로 센다. 합이 목록의 전체 수다 (#95-d) |
| `GET /api/documents/{id}` | 상세 + 텍스트 버전 목록(`versions`: `version`·`created_at`·nullable `author`·`author_via`, MCP `get_document`도 동일) + 청크 수 + 청크 기준 버전 + `files`(원본 판 목록 — 메타데이터만, 바이트는 싣지 않는다. 판마다 `preview_status`: PDF·PNG·JPG·JPEG는 `"ready"`, 변환 대상은 변환본 상태(`ready`·`pending`·`failed`·`unavailable`, 행 없으면 `unavailable`), 그 밖 형식은 `null` — 화면은 이 값만 보고 「미리보기」를 그린다) + `folder`(id·이름·경로 — 조회자가 그 폴더를 볼 수 있을 때만, 아니면 `null`) |
| `PUT /api/documents/{id}/folder` | **문서 폴더 이동.** `{folder_id}`(`null`이면 폴더 밖으로). 문서 소유자만, 쓰기 토큰 허용. 옮길 폴더는 볼 수 있어야 한다. 「폴더 범위 따름」 문서는 새 폴더의 범위로 바뀐다 (ADR-054) |
| `GET /api/documents/{id}/file` · `GET /api/documents/{id}/files/{n}` | **원본 내려받기** — 최신 판 · 특정 판. 항상 `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff`, 미디어 타입은 확장자 고정 매핑. 볼 수 없는 문서·원본 없음·없는 판은 404 (ADR-046) |
| `GET /api/documents/{id}/files/{v}/preview` | **원본 미리보기** — PDF·PNG·JPG·JPEG(파일명 확장자, 대소문자 무관)는 원본을, HWP·HWPX·DOCX·XLSX·PPTX는 **변환본 PDF**를 보낸다. `Content-Disposition: inline` + `X-Content-Type-Options: nosniff` + `Content-Security-Policy: sandbox; default-src 'none'`, 미디어 타입은 확장자 고정 매핑(변환본은 `application/pdf`, 파일명 확장자도 `.pdf`). 순서는 열람 확인(404) → 판 조회(404) → 형식·변환본 확인 → 감사 `original_previewed` → 응답. 변환 대상 판은 변환본이 `ready`면 200, `pending`이면 **409** 「미리보기를 준비 중입니다.」, `failed`면 **415** 「미리보기를 만들지 못했습니다.」, `unavailable`·행 없음이면 **415** 「미리보기할 수 없는 형식입니다.」(그 밖 형식과 같은 문구). 감사는 200일 때만. 공유 토큰은 403(허용 목록 밖) (ADR-058) |
| `PUT /api/documents/{id}/file` | **원본 교체.** multipart `file` + `current_version`. 새 판을 쌓고(이전 판 보존), 추출 텍스트가 달라졌을 때만 새 텍스트 버전(트리거가 이력·잡 생성). 새 원본이 OCR 대상이면 텍스트를 쓰지 않고 추출 잡으로 넘긴다. 최신 판과 같은 바이트면 아무것도 바꾸지 않는다. 버전 불일치·추출 중 409 · 추출 실패 400 · 상한 초과 413 |
| `POST /api/documents/{id}/reextract` | **재추출.** `{current_version}`. 최신 판에서 다시 추출해 결과가 다르면 새 텍스트 버전, 같으면 무변경(`changed: false`). 새 판은 만들지 않는다. 최신 판이 OCR 대상이면 추출 잡으로 넘기고 `changed: false`와 `extraction_status: "pending"`으로 응답한다. 원본 없는 문서·추출 중 문서는 409 |
| `PUT /api/documents/{id}` | 편집된 문서 텍스트(`{content, version}` JSON) → `version`+1, `content`, `content_hash` UPDATE. **버전 이력 기록과 재임베딩 잡 생성은 트리거가 수행.** 요청의 `version`이 현재 버전과 다르면 **409** (아래) |
| `GET /api/documents/{id}/versions/{version}` | 그 텍스트 버전의 본문과 `version`·`created_at`·nullable `author`·`author_via`. 열람 술어를 같은 SQL에 넣어 조회하며 없거나 볼 수 없으면 404 (ADR-037) |
| `GET /api/documents/{id}/versions/{base}/diff/{target}` | **텍스트 버전 비교.** 서버가 줄 단위로 비교한다(`services/textdiff.py` — Myers O((N+M)D), git의 기본 알고리즘, 새 의존성 없음). 응답 `{base, target, identical, too_large, hunks: [{lines: [{op: equal\|added\|removed, text}]}]}` — 바뀐 곳 앞뒤 **3줄**만 맥락으로 싣고(`difflib.get_grouped_opcodes(3)`와 같은 묶음), `text`에는 줄 끝 개행을 넣지 않는다. `identical`은 두 텍스트가 문자열로 완전히 같을 때만 true이고 그때 `hunks`는 빈 배열. **작업량 예산**: 대각선 하나·직진한 줄 하나·역추적 기록 한 칸을 작업 1로 세다가 `MAX_DIFF_WORK`(500만)를 넘으면 그 자리에서 멈추고 `too_large: true`·빈 `hunks`로 답한다 — 쓴 작업량은 예산 + 2 + min(N, M)을 넘지 않는다(jsdiff `maxEditLength`와 같은 방식). 2026-10-08 실측(크기 4.8K~20K줄 × 반복도 × 고친 곳 10~3,000 × 흩어짐/몰림 × 전체 교체): 작업 100만당 **0.12~0.15초·기록 3.8MB**로 일정해 예산이 최악 약 0.75초·19MB를 보장한다. 작업량은 문서 크기가 아니라 바뀐 줄 수(D)에 따라 늘어 대략 D²이며, 고친 줄이 약 1,100줄을 넘으면 too_large다. 실제 규정 2022판↔현행판 21쌍은 최대 6만 작업·8ms다. 처음 쓴 표준 `difflib`(autojunk 끔)는 같은 줄의 짝 수 × 바뀐 대목 수에 비례해, 빈 줄로 나눈 마크다운 2,000문단을 10문단마다 고치면 23.6초였다(같은 입력 Myers 20ms) — 짝 수로 미리 거르는 상한은 바뀐 대목 수 축에서 깨져 버렸다. 계산은 이벤트 루프 밖 스레드에서 돈다. 두 버전 중 하나라도 없거나 볼 수 없으면 404, 공유 토큰은 403 (#190) |
| `PUT /api/documents/{id}/tags` | `{tags: string[]}`로 태그 전체 교체. 트리거는 `UPDATE OF content_hash`에만 걸려 있으므로 **재임베딩을 유발하지 않는다** |
| `DELETE /api/documents/{id}` | **휴지통 이동** — `deleted_at`을 채운다. 소유자만, 쓰기 토큰 허용. `?permanent=true`면 영구 삭제(CASCADE로 벡터·잡·원본 판까지 원자 삭제, 휴지통 밖 문서도 가능) (ADR-060) |
| `GET /api/documents/trash` · `POST /api/documents/{id}/restore` | 내 휴지통 목록(제목·삭제 일시·영구 삭제 예정일) · 복원(`deleted_at`을 지운다, 재임베딩 없음). 소유자만 — 남의 문서는 관리자에게도 404 |
| `POST /api/documents/{id}/reembed` | **임베딩 실패 복구.** 아래 참조 |
| `GET /api/documents/{id}/related` | **관련 문서.** 저장된 관계(`document_edges`)를 읽는다 (ADR-018 개정 · ADR-029). 청크가 없으면 `not_indexed`, edge가 없으면 `no_edges` |
| `GET /api/documents/{id}/tag-suggestions` | **태그 추천.** 관계 이웃의 태그 빈도 (ADR-019). 청크가 없으면 `not_indexed` |
| `GET /api/documents/{id}/links` | **본문이 가리키는 위키링크.** 조회자의 열람 범위에서 해석하며, 대상이 없거나 보이지 않으면 `document_id: null` (ADR-030) |
| `GET /api/documents/{id}/backlinks` | **이 문서를 가리키는 문서.** 열람 가능한 출발 문서만 |
| `POST /api/search` | 하이브리드 검색 + 관계 순회 (아래). 선택 `folder_id`는 그 폴더와 **하위 폴더**의 문서로 직접 결과를 좁힌다 |
| `POST /api/ask` | **근거 기반 답변** — 로그인 필요. 검색과 같은 단일 SQL로 근거를 고른 뒤 답변 프로바이더가 답한다. 선택 `folder_id`는 검색과 같다. `ANSWER_PROVIDER=off`(기본)면 `status: "disabled"` (아래 「근거 기반 답변」, ADR-043) |
| `POST /api/auth/login` · `logout` · `GET /api/auth/me` | 최소 로그인. 세션 토큰은 `sessions` 테이블에 저장 |
| `POST /api/auth/tokens` · `GET /api/auth/tokens` · `DELETE /api/auth/tokens/{id}` | **세션 전용** API 토큰 발급·목록·폐기. 원문은 발급 응답에만 반환하며 기본 scope는 `read` |
| `PUT /api/auth/password` | **세션 전용** 자기 비밀번호 변경. 현재 비밀번호를 확인하고, 바꾼 뒤 그 계정의 세션을 전부 무효화한다. 틀린 현재 비밀번호는 403(세션은 유효하므로 401이 아니다). API 토큰은 폐기하지 않는다 (ADR-040) |
| `GET /api/diagnostics` | **진단.** 고아 문서·깨진 링크·중복 후보 등을 **열람 범위 기준**으로 집계 (ADR-027) |
| `GET /api/clusters` | **관계 지도.** 관계 그래프의 Louvain 군집. 조회 시점 계산, 열람 범위 기준. 이름은 (군집 안 빈도 − 밖 빈도)가 양수인 태그 중 최대, 없으면 중심 문서 제목 (ADR-042 개정) |
| `GET /api/admin/users` 등 | 관리자 전용 |
| `DELETE /api/admin/users/{id}?transfer_to=<username>` | **관리자·세션 전용**. 휴지통 포함 모든 소유 문서·폴더를 이전한 뒤 같은 트랜잭션에서 계정 삭제(204). 공유·토큰은 CASCADE로 삭제. 대상 미지정이면 소유물 있는 사용자는 기존 409, 잘못된 이전 대상은 400. 관리자 열람 부여 없음 (#200) |
| `PUT /api/documents/{id}/owner` `{owner}` | **소유자·세션 전용**. 응답 `{owner_id, still_visible}`. 이전 소유자의 공유에서 제거·새 소유자의 직접 사용자 부여 정리. 보이는 비소유자 403, 안 보이면 404, 본인·없는 대상 400 (#200) |
| `PUT /api/folders/{id}/owner` `{owner}` | **만든 사람·세션 전용**, 관리자 우회 없음. 한 폴더 행만 이전(하위 폴더·문서 그대로), 새 소유자의 사용자 부여 정리. 응답 `{created_by, still_visible}`. 비소유자 403, 안 보이면 404, 본인·없는 대상 400 (#200) |
| `GET /api/admin/audit` | **관리자·세션 전용** 감사 로그 조회. 쿼리 `actor`·`action`(12종, 그 밖은 422)·`limit`(기본 50, 1~200)·`before_id`(id 커서). 응답 `{items, next_before_id}`, id 내림차순. 열람 술어를 걸지 않고 대상 문서 **제목**까지만 보인다(ADR-055 결정 8). 조회 자체는 기록하지 않는다 |
| `POST /api/admin/groups` · `GET /api/admin/groups` · `DELETE /api/admin/groups/{id}` | **관리자·세션 전용**. 그룹 생성·목록·삭제. 이름 변경 없음 (#97 b) |
| `PUT /api/admin/groups/{id}/members/{username}` · `DELETE /api/admin/groups/{id}/members/{username}` | **관리자·세션 전용**. 구성원 추가·제거 (#97 b) |
| `GET /api/principals` | **로그인**. 부여 대상 사용자명·그룹명 목록. 익명은 401 (#97 b) |
| `GET /api/documents/{id}/access` | **로그인·소유자 전용**. 열람 범위 설정 조회. 보이는 비소유자는 403, 안 보이면 404 (#97 b) |
| `PUT /api/documents/{id}/access` | **소유자·세션 전용**. `{visibility, users, groups, follows_folder}` — 폴더 안 문서는 `follows_folder`로 「폴더 범위 따름」↔「개별 지정」을 바꾼다(개별 지정에는 `visibility` 필수, 「폴더 범위 따름」에는 `visibility`·`users`·`groups`를 함께 보내면 400. 지금 「폴더 범위 따름」인 문서에 `follows_folder` 없이 범위만 보내도 400 — 받으면 `visibility` 컬럼만 바뀌고 실효 범위는 폴더 그대로라 저장이 조용히 무시된다). `{visibility, users, groups}`로 전체 교체(저장은 바뀐 부여만 DELETE·INSERT — 감사 기록이 실제 변경만 남도록, ADR-055). 보이는 비소유자는 403, 안 보이면 404. 조직 공개로 바꾸면 사용자·그룹 부여만 삭제하고 공유 부여는 유지 (#97 b·c) |
| `GET /api/folders` | **로그인**. 볼 수 있는 폴더 전체(평평한 목록, `parent_id`로 트리를 만든다). 각 폴더에 최상위 범위 요약 `scope`, 직접 든·볼 수 있는 문서 수 `document_count`, `inherited`(하위 폴더), `can_manage`·`can_change_access` (ADR-054) |
| `POST /api/folders` `{name, parent_id?}` | 폴더 만들기. 쓰기 토큰 허용. 하위 폴더는 볼 수 있는 폴더 아래에 누구나 만든다. 새 최상위 폴더는 조직 공개 |
| `PATCH /api/folders/{id}` `{name}` · `DELETE /api/folders/{id}` | 이름 변경·삭제 — 폴더를 만든 사람 또는 관리자(볼 수 있는 폴더에 한함). 쓰기 토큰 허용. 삭제는 빈 폴더만(하위 폴더나 문서가 있으면 「폴더가 비어 있지 않습니다.」) |
| `GET /api/folders/{id}/access` · `PUT /api/folders/{id}/access` `{visibility, users, groups}` | 최상위 폴더의 열람 범위 조회·교체. **최상위 폴더를 만든 사람만 — 관리자도 불가.** `PUT`은 **세션 전용**, 저장은 바뀐 부여만 반영(감사). 하위 폴더는 범위가 없어 거부 |
| `POST /api/shares` `{name}` · `GET /api/shares` · `DELETE /api/shares/{id}` | 내 공유 생성·목록(포함 문서 id·제목, 토큰 메타)·삭제. 세션 전용 |
| `PUT /api/shares/{id}/documents/{document_id}` · `DELETE /api/shares/{id}/documents/{document_id}` | 공유에 내 문서 넣기·빼기(멱등 204). 세션 전용 |
| `POST /api/shares/{id}/tokens` `{name}` · `DELETE /api/shares/{id}/tokens/{token_id}` | 공유 토큰 발급(원문 1회)·폐기. 세션 전용 |
| `GET /api/system/status` | **로그인 필요 · 운영/데모 전용**: `inet_server_addr()`(현재 접속 노드), pending/processing/error **임베딩** 잡 수(`kind='embed'`), 임베딩 프로바이더명, **정합성 검증 쿼리 결과**(`c.version <> d.version` 건수), **관계 미반영 문서 수**(`kind='edges'` 잡이 `done`이 아닌 문서), **텍스트 인식 대기·실패 문서 수**(`extraction_status`가 `pending`·`failed`), **미리보기 변환 판 수**(`preview_pending`·`preview_failed`·`preview_unavailable` — `document_file_previews.status`별). `/admin/status`가 소비하며 사용자 화면은 호출하지 않는다. SQL과 결과 모델은 `services/system.py`에 있고 라우터는 인증과 응답 변환만 맡는다 |

> **공유 API(#97 c)** — 남의 공유 id는 404, 추가할 문서가 안 보이면 404, 보이는 남의 문서면 403이다. 공유 토큰은 ADR-044 「공유」 결정 5의 읽기 허용 목록만 통과하며 나머지는 403이다. MCP는 바꾸지 않는다.

> **구현 현황**: 위 표의 경로는 모두 구현되어 있다(#97 b 관리 API·#97 c 공유 API·`/api/ask` 포함). 파일 업로드와 JSON 텍스트 공급은 같은 INSERT 헬퍼와 DB 트리거 파생 계약을 공유한다. 프로그램은 사람이 발급한 `read_write` 위임 API 토큰으로 세션 쿠키 없이 텍스트를 공급할 수 있다 (ADR-034·035).
>
> **모든 조회에 열람 범위가 걸린다.** 검색·관련 문서·링크·백링크·진단 집계·클러스터가 같은 `VISIBLE_TO_USER` 술어를 쓴다. 볼 수 없는 문서는 자리 표시조차 남기지 않는다 — 표시 자체가 존재와 개수를 누출한다 (ADR-027).
>
> 새 파일로 교체해도 문서의 id·제목·태그·공개범위·관계는 그대로이고 파일명·유형만 바뀐다 (`PUT /api/documents/{id}/file`, ADR-046).
>
> 라우터는 얇다. 요청 검증과 상태 코드 변환만 하고 실제 로직은 `services/documents.py`·`services/search.py`·`services/system.py` 등에 있으며, MCP 서버가 문서·검색 서비스를 재사용한다. 도메인 예외를 상태 코드로 옮기는 매핑은 `main.py`의 exception handler 한 곳에 있다.

**폴더 (ADR-054, #187)**: 업로드 Form·텍스트 JSON이 선택 `folder_id`를 받는다. 폴더를 고르면 문서는 「폴더 범위 따름」으로 생기고 개별 범위 인자(`visibility`·`grant_users`·`grant_groups`)는 받지 않는다 — 문서 자신의 `visibility`는 `private`로 닫혀 저장되며, 「개별 지정」은 문서 상세에서 한다. 폴더는 멱등키 지문에도 들어간다. 볼 수 없는 폴더는 없는 폴더와 같은 404다. 공유 토큰은 폴더 경로를 쓰지 못한다(허용 목록 밖, 403). MCP 도구는 이번에 바꾸지 않았다.

**생성 시 부여 대상 (#97 b)**: 업로드 Form·텍스트 JSON·MCP `create_document`가
`grant_users`·`grant_groups`(사용자명·그룹명)를 받는다. 쓰기 토큰·MCP도 생성 시 지정할 수 있다.
모르는 이름은 해당 이름을 짚어 400으로 거부한다. `public`에 대상을 보내면 400이며
`visibility=private`로 보내도록 안내한다. 아래 멱등키 지문에도 파일·텍스트 양쪽의 부여 대상을 포함한다.

### 일시 불가용 응답 (모든 엔드포인트)

DB가 잠시 응답할 수 없으면(연결 끊김·페일오버·switchover 중) **`503 Service Unavailable`** + **`Retry-After: 1`** + `{"detail": "일시적으로 요청을 처리할 수 없습니다. 잠시 후 다시 시도하세요."}`로 응답한다. `Retry-After`는 중단 예측값이 아니라 "지금 바로 다시 하지는 마라"는 하한이다(RFC 9110 §10.2.3). 외부 클라이언트는 `Retry-After`보다 일찍 보내지 않는 범위에서 지수 백오프 + 지터로 다시 시도하되, **쓰기 요청은 첫 시도가 이미 커밋됐을 수 있으므로** 아래 멱등키 없이 자동 재시도하지 않는다. 500은 재시도해도 풀리지 않는 결함이다 (ADR-048).

### 멱등키 (`POST /api/documents`, `POST /api/documents/text`)

문서 생성 요청은 선택 헤더 **`Idempotency-Key`**(1~255자)를 받는다. 클라이언트는 사용자 동작(요청) 하나마다 새 키를 만들고, 그 요청의 재시도에는 같은 키를 쓴다 (ADR-047).

| 상황 | 응답 |
|---|---|
| 처음 보는 키 | 평소처럼 문서를 만든다(201). 키는 문서와 **같은 트랜잭션**에 기록된다 |
| 같은 키 + 같은 요청 | 새로 만들지 않고 처음 문서를 **201**로 돌려준다. 본문은 그 문서의 **현재** 요약이다(처음 응답을 저장해 두지 않는다 — 그새 `embedding_status`가 바뀌었을 수 있다) |
| 같은 키 + 다른 요청 | **422**. 요청 비교는 본문 지문(파일은 파일명·바이트 해시·제목·태그·공개범위·부여 대상, 텍스트는 제목·본문·유형·태그·공개범위·부여 대상)으로 하며, 업로드에 쓴 키를 텍스트 공급에 쓰는 것도 다른 요청이다 |
| 같은 키의 동시 요청 | 기본키가 직렬화한다. 뒤 요청은 앞 트랜잭션이 끝나기를 기다렸다가, 앞이 커밋했으면 그 문서를, 롤백했으면 자기가 만든 문서를 받는다 |
| 키 없음 | 지금처럼 동작한다 — 재시도하면 문서가 두 번 생길 수 있다 |

키는 **소유자 범위**다 — 다른 계정의 같은 키와 부딪히지 않고, 그 존재도 드러나지 않는다. 키는 **24시간** 보관되고 워커의 스윕 주기에 지워진다. 그 뒤 같은 키로 다시 보내면 새 문서가 생긴다. 문서를 지우면 그 키도 함께 지워진다(FK CASCADE).

### 빈 파싱 결과 처리 (`POST /api/documents`)

OCR 대상(이미지, 텍스트 레이어가 빈 쪽·글자 정보가 깨진 쪽이 있는 PDF)이 아닌 형식에서 파싱 결과가 공백 제거 후 빈 문자열이면 **400을 반환하고 저장하지 않는다.** 텍스트 레이어가 빈 쪽·글자 정보가 깨진 쪽이 있는 PDF는 여기서 거부하지 않고 OCR로 넘긴다 (「추출 잡」, ADR-052).

```
400 Bad Request
{ "detail": "문서에서 텍스트를 추출하지 못했습니다." }
```

저장 후 `error` 상태로 두는 대안을 택하지 않은 이유: 빈 문서는 임베딩할 것이 없어 **영원히 검색에 잡히지 않는 유령 행**이 되고, 사용자는 목록에서 실패 배지만 볼 뿐 원인을 모른다. 업로드 시점에 즉시 알리는 편이 낫다.
DB 계층에도 `CHECK (extraction_status <> 'done' OR length(btrim(content, E' \t\r\n\f')) > 0)`를 두어 이중으로 막는다 (스키마 절 참조) — 추출이 끝났다고 표시된 문서가 빈 본문인 상태는 DB가 막고, 추출 중·인식 실패 문서만 빈 본문을 허용한다. OCR 결과가 빈 경우는 400이 아니라 문서의 `failed` 표시로 드러난다 — 업로드 요청은 이미 끝났기 때문이다. 여기서 "공백 제거"는 **공백·탭·CR·LF·폼피드**를 뜻한다 — `btrim`의 1인자 형태는 공백만 제거하므로 개행뿐인 추출 결과를 걸러내지 못한다.

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

**원본 파일과 추출 텍스트를 구분한다.** 원본 파일은 `document_files`에 보관되지만 편집 대상이 아니므로, 편집 후에는 `filename = report.pdf`인데 `content`가 그 PDF의 추출 결과와 다른 상태가 될 수 있다. **결함이 아니라 설계된 동작**이며, 스캔 품질이 나쁜 PDF의 오추출을 고치는 정당한 용도가 있다. 재추출(`POST /reextract`)은 그 편집을 원본 기준으로 다시 덮으며, 덮인 텍스트는 버전 이력에 남는다. UI는 편집 영역을 "본문"이 아니라 **"추출 텍스트"**로 표기한다 — 원본 파일이 없는 문서에서는 **"문서 텍스트"**다 (`UI_GUIDE.md`).

### 근거 기반 답변 (POST /api/ask)

#96 a의 구현 계약은 [ADR-043 「구현 형태 (2026-10-04, #96 a)」](ADR.md#구현-형태-2026-10-04-96-a)가 정본이다.
`ask`는 **검색 → 근거 조립 → 프롬프트 → 생성 → 응답**의 고정 파이프라인으로 기존 검색을 소비한다.
검색 SQL·열람 술어·DB 스키마는 변경하지 않고 답을 저장하거나 MCP에 추가하지 않는다.

본문은 `{query, tags?, content_type?, k=5}`이며 빈 질의는 400이다. 로그인 사용자의 세션·위임 토큰을
허용하고 공유 주체는 403으로 막는다(ADR-044 공유 허용 목록). 인증·검색·현재 버전 조회는 커넥션 한 번
대여 안에서 끝낸다. `services/answer.py`의 `gather_evidence`가 `Evidence(hits, sources, system, prompt)`를
반환하면 **커넥션을 반납한 뒤** `generate_answer`를 부른다. 요청 끝까지 연결을 쥐는 `Connection` 의존성을
쓰지 않고, 빌린 커넥션 안에서 `current_user`·`require_user_id`를 함수로 직접 부른다. 생성은 동기
`AnswerProvider.generate(system, prompt)`를 `asyncio.to_thread`로 실행한다.

현재 `documents.version`은 같은 커넥션에서 `VISIBLE_TO_USER`와 함께 별도로 조회하고, 그 사이 열람에서
빠진 문서의 근거는 버린다. 검색 순위대로 `passages`(없으면 대표 청크)를 쓰고 동일 본문은 한 번만 넣는다.
`via=revision` 히트는 본문이 직전 판 전문이므로, 직전 판을 워커와 같은 청킹으로 나눠 검색이 맞춘 현재 판 청크와
어절이 가장 많이 겹치는 청크 하나를 근거로 쓴다(`chunk_index`도 직전 판 기준).
`ANSWER_CONTEXT_CHARS` 예산을 지키며 첫 근거가 너무 길면 잘라서 하나는 넣는다. 300자 미리보기 대신
본문 후보를 사용한다. 근거 없음 안내는 프롬프트 지시이며 생성 결과의 보장이 아니다.

답변 결과는 200과 `{status, answer, detail, sources[], items[]}`로 반환한다. `items`는 `/api/search`의
`SearchResult`와 같은 모양이며 모든 상태에서 검색 결과를 그대로 돌려준다. 판정 순서는 다음과 같다.

- `disabled`: 프로바이더 None. 모델을 부르지 않는다.
- `no_evidence`: 근거 0건. 모델을 부르지 않는다.
- `answered`: 생성된 답변과 근거를 반환한다.
- `failed`: 연결 거부·타임아웃·HTTP 오류·빈 응답을 묶은 `AnswerUnavailable`. `detail`은 고정 문구이고 원인은 서버 로그에만 남긴다. 다른 예외는 삼키지 않는다.

`sources`는 모델에 준 대목 전체다. 각 항목은 `label`(1부터, 답의 `[n]`과 대응)·`document_id`·`title`·
`chunk_index`·`based_on_version`·`current_version`·`revised`·`content`·`cited`를 담는다. `content`는
모델에 준 텍스트 그대로이고, `revised`는 근거 버전이 현재 버전보다 작은지 나타낸다. `cited`는 기본 False이며
답에서 해당 라벨을 실제 인용했는지 구분한다. 직전 텍스트 버전 결과도 기준 버전을 보존한다.

인증·입력 오류는 기존 오류 코드를 쓰고, 503은 DB 일시 불가용에만 쓴다(ADR-048). 재시도 미들웨어의
읽기 대상에 `/api/ask`를 넣는다. DB 단계가 생성 전에 끝나므로 DB 재시도가 생성을 두 번 돌리지 않는다.

## 검색 데이터 흐름

질의 텍스트 → 동일 프로바이더로 질의 임베딩 → **단일 `WITH RECURSIVE` SQL**. 벡터 후보 확보와 관계 순회가 한 쿼리 안에서 끝난다.

```sql
BEGIN;  -- ★ plain BEGIN. READ ONLY 금지 (아래 설명)

SET LOCAL hnsw.ef_search = 200;      -- 필터 통과 후보를 충분히 확보 (기본 40)
SET LOCAL random_page_cost = 1.1;    -- 무필터 검색이 HNSW를 타게 한다 (ADR-011 보강 5)
SET LOCAL jit = off;                 -- 부여 서브플랜이 JIT 임계를 넘겨 컴파일이 붙는다 (ADR-044)
SET LOCAL hnsw.iterative_scan = strict_order;  -- 좁은 열람 범위·폴더 필터가 후보를 굶기지 않게 (ADR-011 2026-10-06 개정)

WITH RECURSIVE candidates AS (       -- ① 벡터 후보 (k * 5). 필터를 여기 안에 둔다
    SELECT c.document_id, c.chunk_index,
           ( … 앞뒤 청크를 이어붙인 발췌 … ) AS content,
           c.version, c.embedding <=> %(qvec)s::vector AS dist
    FROM document_chunks c JOIN documents d ON d.id = c.document_id
    WHERE (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)
      AND (%(ctype)s::text IS NULL OR d.content_type = %(ctype)s)
      AND ( … 폴더 필터: 볼 수 있는 폴더이고, 문서의 폴더에서 위로 올라가 그 폴더에 닿는다(상관 재귀) … )
      AND ( … services/visibility.py의 VISIBLE_TO_USER … )
    ORDER BY c.embedding <=> %(qvec)s::vector
    LIMIT %(k)s * 5
),
resolved_links AS (                  -- ② 위키링크를 열람 범위에서 edge로 해석 (ADR-030)
    SELECT l.src_document_id, d.id AS dst_document_id, 'refers'::text AS kind, …
    FROM document_links l
    JOIN documents d ON d.title = l.target_title
                    AND ( … services/visibility.py의 VISIBLE_TO_USER … )
),
traversal_edges AS (                 -- ③ 저장된 관계(양방향) ∪ 해석된 링크
    SELECT e.src_document_id, e.dst_document_id, e.kind, e.dst_chunk_index FROM document_edges e
    UNION ALL                        -- 역방향. 저장은 단방향이라 여기서 뒤집는다 (ADR-029 개정)
    SELECT e.dst_document_id, e.src_document_id, e.kind, e.src_chunk_index FROM document_edges e
    UNION ALL
    SELECT … FROM resolved_links
),
walk_ids AS (                        -- ④ 깊이 2까지 순회. 한 단계마다 거리에 +2.0
    SELECT document_id, dist, …, 0 AS depth FROM candidates   -- 시작점
    UNION ALL
    SELECT e.dst_document_id, w.dist + 2.0, …, w.depth + 1, e.dst_chunk_index
    FROM walk_ids w JOIN traversal_edges e ON e.src_document_id = w.document_id
    …                                             -- path 배열로 순환을 막는다.
),                                                --   문서 id·거리만 나른다 — 청크는 아직 고르지 않는다
walk_targets AS (                    -- ④' 문서·대상 청크 단위로 접는다 (가장 가까운 경로, <kind 순서>)
    SELECT DISTINCT ON (document_id, COALESCE(target_chunk_index, -1)) … FROM walk_ids WHERE depth > 0
),
walk AS (                            -- ④'' 접힌 행에서만 발췌 청크를 고른다 (ADR-011 보강 6)
    SELECT … FROM candidates
    UNION ALL
    SELECT … FROM walk_targets w
    JOIN LATERAL (SELECT … FROM document_chunks WHERE document_id = w.document_id
                  AND (w.target_chunk_index IS NULL OR chunk_index = w.target_chunk_index)
                  ORDER BY embedding <=> %(qvec)s::vector LIMIT 1) target ON true
),
expanded AS (                        -- ⑤ 직전 텍스트 버전을 revision으로 더한다
    SELECT … FROM walk
    UNION ALL
    SELECT …, 'revision' FROM candidates JOIN document_versions …
),
deduplicated AS ( … ),               -- ⑥ 문서·버전·청크 단위로 1건
selected AS (                        -- ⑦ 직접 결과와 확장 결과를 각각 LIMIT k
    (SELECT * FROM deduplicated WHERE depth = 0 ORDER BY <제목 우선순위>, dist, … LIMIT %(k)s)
    UNION ALL
    (SELECT * FROM deduplicated WHERE depth > 0 ORDER BY dist, <kind 순서>, … LIMIT %(k)s)
)
SELECT … FROM selected hit JOIN documents d ON d.id = hit.document_id
ORDER BY CASE WHEN hit.depth = 0 THEN 0 ELSE 1 END,   -- 직접 결과가 항상 먼저
         <직접 결과만 제목 우선순위 적용>, hit.dist, <kind 순서>, hit.depth, …;

COMMIT;
```

> 실제 쿼리는 `services/search.py`의 `SEARCH_SQL` **한 곳에만** 존재하며 REST API와 MCP 서버가 공유한다. 위는 단계 구조만 옮긴 것이다 — 컬럼 목록과 순환 방지 `path` 배열은 코드를 보라. **문서에 전체 SQL을 복사해 두지 않는다**: 쿼리가 길어진 뒤로는 복사본이 조용히 낡아 잘못된 근거가 된다.

정형 필터·권한 술어·벡터 정렬·관계 순회가 한 쿼리에 결합된다(가산점 포인트).

**직접 결과의 제목 우선순위:** 질문 전체가 제목과 같거나, 질문에 단일 발행 판본(`2022판`·`2022년판`)과 문서명이 있고 제목 끝에도 같은 `(2022판)`이 있으면 기존 벡터 후보 안에서 먼저 표시한다. 날짜 없는 제목을 최신판으로 추정하지 않으며, 여러 판본을 비교하는 질문에는 판본 우선순위를 적용하지 않는다. 유사도 점수·발췌 선택·관계 결과 정렬은 그대로다. 그다음 우선순위로, 단일 ADR·조항 번호와 정확한 문서명을 함께 지정한 질문은 해당 문서명을 제목에 가진 직접 후보를 먼저 표시한다. 번호 제목만으로 ADR 원문을 무조건 우선하지 않는다 — 실측·근거 변경을 요청한 질문에서 조사 문서를 밀어내는 문제가 확인됐다. 후보 밖 문서를 새로 가져오지 않고, 권한·태그·유형 필터를 통과한 직접 후보의 순서만 바꾼다.

**번호 질문의 발췌:** 단일 `ADR-006` 또는 `제9조`를 명시한 질문은, 이미 선택된 직접 결과 문서의 같은 텍스트 버전에서 번호가 있는 청크를 찾아 앞뒤 청크와 함께 표시한다. 일치하는 청크가 여러 개면 질의 벡터에 가장 가까운 것을 선택한다. 문서 순위와 유사도 점수는 기존 벡터 검색 기준이며, 발췌 중심 청크 번호만 실제 선택한 청크로 바뀐다. 번호가 없거나 여러 번호를 비교하면 기존 발췌를 유지한다. 관계 결과·직전 텍스트 버전의 발췌는 바꾸지 않는다. 번호 언급을 찾는 규칙이며 해당 조항의 전체 내용이나 답변 정확도를 보장하지 않는다.

**미리보기와 문맥:** `content`는 기존 전체 발췌이며, 선택한 문서·버전의 주변 청크 안에서
질의어에 맞는 최대 300자 대목을 `preview`로 추가한다. 이웃 청크를 연결한 문맥에서 기존 분할과
행 시작의 창을 비교해 표 행·문단 및 청크 경계에 걸친 설명을 보존한다. 한국어 단어 내부의
문자 쌍과 영문·숫자 토큰의 포함 수를 비교한다. 문서 순위·유사도는 바꾸지 않는다.
선택한 대목이 Markdown 표 행에서 시작하고 바로 앞 행도 데이터 행이면, 두 행의 원문이
300자 이내일 때 앞 행부터 선택 행 끝까지 표시한다. 구분선·일반 문장·긴 행은 보완하지 않는다.
번호 질문의 미리보기는 위 SQL이 선택한 중심 청크의 번호에서 시작한다. 미리보기가 있으면
`chunk_index`는 그 대목이 시작하는 청크를 가리키며 `based_on_version`은 유지한다. 질의어 일치가
없거나 관계·이전 판 결과이면 `preview`는 null이다. 웹은 미리보기를 보여주고 전체 `content`를
펼쳐 읽게 한다. 이것은 의미 기반 답변 선택이 아니며, 질문의 답이 기존 후보 대목 밖에 있으면
찾지 못한다 (ADR-011 보강 7).

**추가 본문 후보:** 직접 결과의 `passages`는 기존 `k * 5` 벡터 후보에서 같은 문서·버전의
중심 후보를 거리순(동점은 청크 번호순)으로 최대 8개 고른다. 같은 버전의 주변 ±2청크를
조회하고, 겹치거나 이어지는 문맥은 청크 번호로 합쳐 각 원래 청크를 한 번만 반환한다.
각 그룹의 `chunk_index`·`score`는 그 안에서 벡터 점수가 가장 높은 중심 후보의 값이고,
`content`는 그룹의 청크 번호순 문맥, `based_on_version`은 공통 버전이다. 그룹도 점수순이며
최종 항목 수는 중심 후보 수보다 적을 수 있다. 한 그룹은 여러 중심 후보의 문맥을 포함할 수 있어
5청크보다 길 수 있지만, 기존 후보 문맥 밖의 청크를 추가하지 않는다.
별도 벡터 검색·문서 전체 스캔·추가 임베딩 없이 기존 candidates CTE와 제한된 주변 조회를 사용한다.
관계·이전 판 결과는 빈 배열이다. 작은 `k`에서는 전체 후보 한도 때문에 중심 후보 수도 줄고,
중심 후보의 ±2 밖에 있는 답은 가져오지 못한다. 이 본문 병합은 첫 발췌·미리보기·문서 순위를
유지한다. 미리보기는 기존 발췌 범위에서 별도로 선택하며, 후보 전체를 어휘 점수나 추가 모델로
재순위하는 시도는 장애·문서 수정 사례에서 오선택이 남아 채택하지 않았다.
웹도 같은 응답의 `passages`를 「검색된 본문 대목」 펼치기로 표시한다. 서버가 합친 본문을
다시 300자로 자르지 않고 텍스트 버전과 함께 보여준다. 출처는 해당 결과 카드의 문서 상세
링크이며, 첫 미리보기·문서 순위·점수는 유지한다. 후보가 없거나 관계 결과이면 펼치기를 만들지 않는다.


**③에서 `document_edges`를 두 번 읽는다.** 저장이 단방향(`src` = 계산 주체)이라 역방향은 저장돼 있지 않다. 뒤집을 때 `src_chunk_index`가 대상 청크가 된다 — `related`의 위치 정보는 계산 주체 쪽 대목이 `src_chunk_index`이므로, 반대편에서 볼 때는 그것이 "닿은 대목"이다. 관계가 무방향이라는 뜻은 저장이 아니라 이 CTE가 지킨다 (ADR-029 개정).

**`<kind 순서>`는 동점 타이브레이커다** — `overlaps`(0) · `related`(1) · `refers`(2) · `revision`(3). 사람이 본문에 직접 쓴 링크가 같은 문서의 과거 판본보다 앞선다 (ADR-030). 최종 정렬뿐 아니라 ④'·⑥에서 같은 문서·같은 발췌로 수렴한 행을 하나로 접을 때도 이 순서를 쓴다 — 문서 단위 `overlaps`와 위키링크 `refers`가 같은 경유 문서에서 닿으면 거리·깊이까지 같은 동점이 실제로 생긴다 (ADR-011 보강 6).

**순회 행에서는 청크를 고르지 않는다 (④ → ④' → ④'').** 순회가 발췌 청크까지 골라 나르면 촘촘한 그래프에서 깊이 2 순회 행(당시 시연 코퍼스 76문서·관계 1,704건에서 13k행)마다 청크 정렬이 돌아 그것이 검색 비용의 전부가 된다. 문서·대상 청크 단위로 접은 뒤(204행)에만 청크를 고르면 결과는 같고 실 OpenSQL VM에서 SQL이 3.5~11.6초 → 0.35~0.81초가 된다.

**한 단계마다 거리에 `GRAPH_DISTANCE_PENALTY = 2.0`을 더한다.** 코사인 거리의 최대값이 2이므로, 관계로 닿은 문서가 직접 벡터 결과를 절대 앞지르지 못한다. `score = 1 - dist` 정렬 의미를 그대로 두면서 층을 가르는 방법이다.

### 네 가지 설계 결정이 이 쿼리에 반영되어 있다

**1. `BEGIN … COMMIT`으로 감싼다 (ADR-010)**

OpenProxy는 `query_parser_read_write_splitting` 활성 시 **트랜잭션 밖의 단순 `SELECT`를 Replica로 라우팅**하며, 복제 지연 보장이 없다. 워커가 Primary에 청크를 커밋한 직후 검색하면 방금 임베딩된 청크가 누락될 수 있다.

> ⚠️ **`BEGIN READ ONLY`를 쓰면 안 된다.** OpenProxy 1.1.3부터 `BEGIN READ ONLY`와 `START TRANSACTION READ ONLY`는 **의도적으로 Replica로 라우팅**된다. "읽기 전용이니 READ ONLY로 선언하는 게 맞다"는 직관을 따르면 정확히 반대 결과가 나온다.

**2. 네 개의 `SET LOCAL` — `hnsw.ef_search`·`random_page_cost`·`jit`·`hnsw.iterative_scan` (ADR-011 보강 4·5·2026-10-06 개정, ADR-044)**

> 2026-10-06: 처음 셋에 `hnsw.iterative_scan = strict_order`가 더해졌다(아래 마지막 문단). 넷은 `apply_vector_search_settings` 한 곳에서 건다.

`ef_search = 200`: HNSW 인덱스는 `document_chunks`에 있는데 필터는 JOIN 상대인 `documents`에 있다. 기본 `ef_search = 40`으로는 태그 필터가 조금만 좁아도 `LIMIT k`를 채우지 못한다. 후보 풀을 키워 이를 완화한다. `SET LOCAL`이므로 트랜잭션이 끝나면 자동 복원된다 — ①의 명시적 트랜잭션이 여기서 한 번 더 쓸모가 있다.

`random_page_cost = 1.1`: VM 기본값 4에서는 플래너가 HNSW를 아예 고르지 않는다. 힙이 3MB인데 인덱스가 47MB라, 임의 접근을 4배로 계산하면 통째로 읽는 쪽이 싸다고 나온다. **태그·유형 필터는 선택적**(`%(tags)s IS NULL OR …`)이므로 이 쿼리에는 필터 없는 경로가 항상 존재하며, 그 경로가 인덱스를 타느냐가 여기 달려 있다. 전역이 아니라 `SET LOCAL`로 거는 이유는 OpenProxy가 백엔드 반납 시 `RESET ALL`만 하고 `DISCARD ALL`은 하지 않아(§5-2) 세션 GUC에 의존하지 않는 편이 안전하기 때문이다.

`jit = off`: 열람 술어의 부여 서브플랜(ADR-044)이 추정 비용을 `jit_above_cost`(100,000) 위로 올리면 JIT가 있는 PostgreSQL에서 몇 ms짜리 검색에 컴파일 수십 ms가 붙는다. 비용은 데이터 규모에 비례해, 작은 설치에서는 안 보이다가 조직이 커지면 나타난다. 실 OpenSQL 17.8은 LLVM 모듈이 없어(`pg_jit_available() = false`) 해당하지 않지만 설치 대상인 표준 PostgreSQL에는 있다.

`hnsw.iterative_scan = strict_order`: 필터가 HNSW 뒤에 걸리므로, 열람 범위가 좁은 사용자(제한 폴더가 정상 사용법이 되면 흔하다)는 후보 `k * 5` 중 대부분이 걸러져 `LIMIT`을 채우지 못했다 — 문서의 5.9%만 보는 사용자의 후보가 100행 중 11.5행, recall@10 0.40. `iterative_scan`은 통과한 행이 모자라면 인덱스를 더 훑어 1.00으로 올렸다(폴더 필터 0.27 → 0.99). 거리순을 보장하는 `strict_order`를 쓴다 — 후보 CTE 뒤의 `DISTINCT ON`·재정렬이 그 순서에 기댄다. 비용은 로컬에서 측정 오차 수준, x86 에뮬레이션 VM에서 약 1.5배였다. `MAX_K * 배수 < EF_SEARCH` 불변식은 안전망으로 그대로 둔다.

**폴더 필터는 직접 결과에만 걸린다.** `folder_id`를 주면 후보 CTE가 그 폴더와 하위 폴더의 문서로 좁혀지고, 관계로 확장된 결과(깊이 1·2)는 폴더 밖 문서도 나올 수 있다 — 열람 술어는 그대로 걸린다. 볼 수 없는 폴더를 주면 아무것도 나오지 않는다.

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

> **2026-08-11에 방식이 바뀌었다.** 원래는 대상 문서의 `avg(embedding)`을 질의 시점에 계산해 이웃을 찾았다. 그 방식을 고른 근거는 *"조회 시점 계산이라 항상 현재 청크를 따른다"*였고, 저장된 관계로 바꿀 때의 근거는 관계 edge를 **청크 교체와 같은 트랜잭션에서** 만드는 트리거라 최신성 차이가 없다는 것이었다. **그 근거는 016·017로 관계 판정이 잡으로 분리되면서 사라졌다** — 청크 교체와 관계 반영 사이에 다시 시차가 있다 (ADR-029 결정 3 개정). 그래도 저장된 관계를 읽는 선택은 유지한다: 이 제품이 약속한 보장은 원래 버전 일관성 + 최신 수렴이고(ADR-015), 아직 반영되지 않은 문서 수는 `/api/system/status`가 세어 관측할 수 있다. 문서 대표 벡터를 컬럼으로 저장하지 않는다는 원래 판단도 그대로다 — edge는 벡터가 아니라 관계다.
>
> 따라서 이 절의 두 쿼리에는 **벡터 연산이 없고, `SET LOCAL` 두 줄도 필요 없다.** 벡터 정렬은 청크가 바뀐 뒤 관계 잡이 한 번 수행하며 그 구조는 「자동 임베딩 파이프라인」의 관계 생성 절에 있다.

### 세 가지 공통 규칙

**1. 권한 필터를 검색과 동일하게 적용한다**

`services/visibility.py`의 `VISIBLE_TO_USER`를 재사용한다. 사용자에게는 공개·소유자·사용자/그룹
열람 부여를, 공유 주체에게는 해당 공유에 지정된 문서만 허용한다. 공개/소유자 조건만 복사하면
열람 부여와 공유 경로의 정책이 검색과 달라진다.

빠뜨리면 관련 문서가 private 문서를 노출하고, 태그 추천이 private 문서의 태그를 흘린다. 대상 문서 자체도 서비스가 `ensure_visible`로 검증한다. 현재 이 검증은 `find_related`·`suggest_tags`·`resolve_links`·`find_backlinks` 네 함수에 걸려 있어 HTTP를 거치지 않는 호출에도 같은 404 의미의 `DocumentNotFound`가 적용된다.

**2. `kind`를 섞어 `score`로 정렬하지 않는다**

```sql
ORDER BY b.kind, b.score DESC, d.id     -- kind로 묶은 뒤 그 안에서 점수순 (b = 양방향 이웃을 접은 best CTE)
```

`score`의 **척도가 `kind`마다 다르기** 때문이다 — `overlaps`는 매칭 비율, `related`·`points_to`는 `1.0 - 최소거리`다 (`006_edges_tables.sql`). 섞어서 정렬하면 서로 다른 단위의 숫자를 한 줄에 세우게 된다. 화면도 `kind`별로 묶어 보여준다.

> 벡터 정렬 후보를 `DISTINCT ON`으로 줄이는 순서 규칙(ADR-011 보강 1)은 이 절에 더 이상 해당하지 않는다 — 여기에는 벡터 정렬이 없다. 그 규칙이 살아 있는 곳은 **검색 쿼리**이며 「검색 데이터 흐름」 절에 있다.

**3. 청크가 없는 문서는 관계를 조회하지 않는다**

관계 edge는 청크가 준비된 뒤 별도 관계 잡에서 생성된다. 청크가 0행이면 **아직 색인 전**임을
구분해 알린다. 청크가 있어도 관계 잡이 끝나기 전에는 관계가 비어 있거나 이전 판정일 수 있다.

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
  AND ( … services/visibility.py의 VISIBLE_TO_USER … )
ORDER BY d.created_at, d.id;

-- 3) 저장된 관계 — 벡터 정렬 없이 edge를 읽기만 한다. 저장이 단방향이라 양쪽에서 읽는다
WITH neighbors AS (
    SELECT e.dst_document_id AS document_id, e.kind, e.score
    FROM document_edges e WHERE e.src_document_id = %(id)s
    UNION ALL                                  -- 역방향 — 남이 발견한 관계도 이 문서의 관련 문서다
    SELECT e.src_document_id, e.kind, e.score
    FROM document_edges e WHERE e.dst_document_id = %(id)s
),
best AS (                                      -- 같은 이웃이 양쪽에 있으면 overlaps 우선, 같은 kind면 높은 점수
    SELECT DISTINCT ON (document_id) document_id, kind, score
    FROM neighbors
    ORDER BY document_id,
             CASE kind WHEN 'overlaps' THEN 0 WHEN 'related' THEN 1 ELSE 2 END,
             score DESC
)
SELECT d.id, d.title, d.tags, b.kind, b.score
FROM best b
JOIN documents d ON d.id = b.document_id
WHERE ( … services/visibility.py의 VISIBLE_TO_USER … )   -- ★ 열람 범위는 조회 시점에
ORDER BY b.kind, b.score DESC, d.id
LIMIT %(k)s;

COMMIT;
```

> **권한은 저장이 아니라 조회에서 건다.** 워커에는 사용자 컨텍스트가 없으므로 edge 자체는 권한과 무관하게 만들어진다. 열람 범위는 **읽을 때** 적용하며, 비공개 문서로 향하는 edge는 그 사용자에게 없는 것처럼 보인다 (ADR-027).

**`score`가 무엇인지 정확히** — `kind`마다 척도가 다르다. 하나의 유사도 축이 아니다.

| `kind` | `score`의 의미 | 계산 |
|---|---|---|
| `overlaps` | 자기 대목 중 상대 문서에서 최근접 이웃을 찾은 **비율**(판정은 양쪽 비율 모두 ≥ 0.8이어야 하지만 저장값은 계산 주체 쪽이다) | `overlap_ratio` |
| `related` · `points_to` | 가장 가까운 청크 쌍의 **유사도** | `1.0 - 최소거리` |

`014_edges_triggers.sql`의 `CASE WHEN is_overlaps THEN overlap_ratio ELSE 1.0 - min_dist END`가 그 자리다. **비율과 거리는 같은 줄에 세울 수 없으므로 `kind`를 섞어 정렬하지 않는다**(공통 규칙 2).

> **`overlaps`를 "같은 내용"으로 읽으면 안 된다.** 이웃 판정이 순위 기반이라 절대 거리 임계가 없고, 주제가 가까운 문서끼리는 모든 대목이 서로 최근접이 되어 비율이 1.0에 붙는다 — 실 BGE-M3 실측에서 `PRD`↔`UI 디자인 가이드`가 1.00이었다 (`OPENSQL_RESEARCH.md` §14). 화면 어휘를 「여러 대목에서 만난다」로 두고 대목 수만 달리 말하는 이유다 (`UI_GUIDE.md`).

### 유사 후보 표시 — 중복 "탐지"가 아니다

위 `score`는 **중복 판정에 쓰지 않는다.** 완전히 같은 문서도 주제가 여럿이면 점수가 낮게 나올 수 있고(거짓 음성), 긴 문서가 짧은 문서를 포함하면 높게 나온다(포함이지 중복이 아님, 거짓 양성).

**한 방향만 저장하고 조회에서 합친다.** `score`는 계산 주체(`src`) 기준 값이며 반대편에서 계산한 값과 다를 수 있다 — kNN은 대칭이 아니다. 같은 이웃이 양방향에서 각각 발견되어 kind가 다르면 `overlaps`를 남기고, 같은 kind면 높은 점수를 남긴다(`best` CTE). 어느 쪽 값을 보든 두 문서 사이의 관측 하나이지 대칭 유사도가 아니므로, 중복 판정처럼 정밀도가 필요한 곳에 쓸 수 없다 (ADR-029 개정).

| 신호 | 판정 | 방법 |
|---|---|---|
| `content_hash` 완전 일치 | **동일 텍스트** — 확정 | `SELECT id, title FROM documents WHERE content_hash = %(hash)s AND id <> %(id)s` |
| `score` 상위 항목 | **내용이 유사한 문서** — 참고용 | 위 관련 문서 쿼리 |

자동 차단·병합·업로드 거부를 붙이지 않고, 고정 임계값으로 "중복" 배지를 켜지도 않는다. UI 문구는 **"내용이 유사한 문서가 있습니다"**이며 판단은 사람이 한다.

### 태그 추천

태그를 임베딩하지 않는다. 유사 문서를 찾은 뒤 **그 문서들의 태그를 빈도순**으로 제시한다 (ADR-019).

```sql
BEGIN;  -- plain BEGIN (ADR-010). 관련 문서와 같은 이유로 SET LOCAL이 없다

WITH neighbors AS ( … ),       -- 관련 문서와 같은 CTE: src ∪ dst 양방향
best AS ( … ),                 -- 같은 이웃은 overlaps 우선 1건
selected_neighbors AS (        -- 저장된 관계에서 이웃 10건 (NEIGHBOR_LIMIT)
  SELECT b.document_id
  FROM best b
  JOIN documents d ON d.id = b.document_id
  WHERE ( … services/visibility.py의 VISIBLE_TO_USER … )
  ORDER BY b.kind, b.score DESC, d.id      -- 관련 문서와 같은 정렬 (공통 규칙 2)
  LIMIT 10
)
SELECT t.tag, count(*) AS freq
FROM selected_neighbors n
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
- **프로바이더를 만드는 프로세스는 기동 시 `warm_up()`으로 예열한다** — API(lifespan)와 워커다. `LocalProvider`가 첫 `embed()`까지 모델 로딩을 미루므로, 예열이 없으면 그 지연이 통째로 첫 요청에 붙는다: API는 **첫 검색 12.5초**(2026-08-21 실측, 예열 후 0.2초), 워커는 첫 업로드의 처리 지연이다. 예열 실패는 삼킨다 — 최적화이지 새 실패 지점이 아니며, 모델을 받을 수 없으면 기존 실패 경로가 더 정확히 알린다.
- **stdio MCP는 예열하지 않는다. 원격은 API가 예열한 프로바이더를 쓴다.** stdio 서버라 기동이 12초 늦어지면 클라이언트의 기동 타임아웃에 걸릴 수 있고, 첫 호출을 기다리는 것은 사람이 아니라 에이전트다. 첫 `search_documents` 호출이 로딩을 떠안는 것을 한계로 받아들인다.

### 청킹 (services/chunking.py)

순수 함수. 청크 최대 1,000자, 인접 청크 150자 오버랩을 유지하면서 경계 후보를 **문장이 끝난
문단 경계 > 문장 끝·조문 머리 > 문장 중간 문단 경계 > 강제 절단**의 네 등급으로 고른다. 같은
등급에서는 뒤쪽 경계를 선택하며, 앞 조각을 ATX 헤딩 줄로 끝내는 자리(헤딩 뒤 빈 줄·헤딩 안의
문장 부호·헤딩으로 끝나는 혼합 문단)는 등급과 무관하게 제외해 헤딩이 앞 청크 꼬리에 고립되지
않게 한다 (ADR-045). 외부 의존성 없이 단위 테스트 가능해야 한다.

## 프론트엔드 패턴

- 사용자 화면: `/`(목록 + 업로드 드롭존), `/documents/[id]`(메타데이터·텍스트 버전 이력·청크 수와 기준 버전 요약·**문서 텍스트 편집**·관련 문서·태그 추천), `/search`(질의 + 태그/유형 필터 + 결과. "실행된 SQL 보기" 토글. 로그인 사용자에게는 마지막 검색 입력으로 `POST /api/ask`를 부르는 **근거 기반 답변 패널** — `AnswerPanel`), `/clusters`(관계 군집 덩어리와 연결), `/diagnostics`(고아·중복 후보·미분류·깨진 링크), `/login`, `/settings`(자기 비밀번호 변경·API 토큰·외부 공유)
- **편집은 Client Component**다. 보기 ↔ 편집 토글, 저장 시 `version`을 함께 전송하고 409를 처리한다. 저장 직후 상태 배지가 `pending → processing → ready`로 바뀌는 것을 2초 폴링으로 보여준다
- **사용자 화면은 인프라 상태를 노출하지 않는다.** 페일오버가 나도 화면 구성이 달라지지 않으며, 사용자는 업로드·검색이 계속 성공하는 것만 본다 (UI_GUIDE 디자인 원칙 3).
- **읽기 요청은 503·네트워크 오류를 백오프로 기다린다**(`lib/api.ts`). 읽기 함수는 `signal`을 받고, 훅과 화면 진입 시 조회는 마운트 동안 `AbortController` 하나로 묶어 **화면을 떠나면 진행 중인 요청과 백오프 대기를 함께 취소한다** — 쓸 곳 없는 재시도가 서버에 부하를 더하거나 재시도 안내가 다른 화면에 남지 않게 한다. 새 검색은 이전 검색을 취소한다. 사용자 동작으로 시작한 읽기(버전 본문 열기, 발급·생성 뒤 목록 다시 받기)도 `useUnmountSignal`로 같은 규칙을 따른다 — 앞선 쓰기 요청은 이미 커밋됐을 수 있어 취소하지 않는다. 기다리는 동안에만 `RetryNotice`가 「연결이 원활하지 않아 다시 시도하는 중입니다.」를 보이고 성공하면 사라진다. 무엇이 멈췄는지는 말하지 않는다. `/admin/status`의 `GET /api/system/status`만은 백오프하지 않는다 — 장애를 바로 드러내야 하는 관측 채널이고, 2초 폴링 자체가 재시도다 (ADR-048 결정 4, UI_GUIDE 원칙 3).
- 관리 화면: `/admin/status` — `GET /api/system/status`를 폴링해 접속 노드·잡 수·프로바이더 표시. **페일오버 데모의 증거 채널**이며 사용자 내비게이션에 노출하지 않는다(→ #190에서 관리자 메뉴 노출로 개정 예정). `/admin/users` — 계정 발급·삭제 (ADR-028). `/admin/groups` — 그룹 생성·삭제와 구성원 관리 (ADR-044)
- 화면은 모두 Client Component다. 로그인 세션 확인과 목록·상세·관리 화면의 폴링 때문이다
- API 연동은 `next.config.js` rewrites로 FastAPI 프록시

## 상태 관리

- 서버 상태: fetch 기반. 목록과 `/admin/status`는 상시 2초 폴링하고, 문서 상세는 `pending`·`processing`일 때만 2초 폴링하며 `ready`·`error`에서는 멈춘다. 폴링 실패 시 마지막 성공 데이터를 유지한다. SSE/웹소켓 사용 안 함
- 클라이언트 상태: useState만 사용하고 전역 상태 라이브러리는 두지 않는다. 로그인 사용자는 서버 세션 쿠키가 정하며(ADR-028), 화면은 `GET /api/auth/me`로 확인만 한다
