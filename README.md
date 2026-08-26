# OpenArchive

**문서를 고치면 DB가 벡터를 따라 맞추는, AI를 위한 문서 데이터 플랫폼**

> AI에게 주는 근거가 어느 시점에도 **하나의 일관된 버전**이고, **최신 버전으로 수렴**함을 데이터베이스가 보장합니다.

[![CI](https://github.com/jeongeundev/OpenArchive/actions/workflows/ci.yml/badge.svg)](https://github.com/jeongeundev/OpenArchive/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PostgreSQL](https://img.shields.io/badge/OpenSQL-PostgreSQL%2017-336791.svg)](https://docs.tibero.com/tmaxopensql/overview)
[![Embedding](https://img.shields.io/badge/embedding-BGE--M3%20(MIT)-orange.svg)](https://huggingface.co/BAAI/bge-m3)

> 2026년 오픈소스 개발자대회 기업 지정과제 출품작
> **현재 상태**: DB 계층(트리거·잡 큐·워커), 백엔드 API·하이브리드 검색, 관련 문서·태그 추천, MCP 서버와 프론트엔드(문서 목록·상세·검색·클러스터·진단과 관리 화면)가 동작합니다.

---

```mermaid
flowchart LR
    ui["Web UI<br/>Next.js"]
    rest["REST Client<br/>스크립트 · 커넥터"]
    mcpc["MCP Client<br/>AI 에이전트"]

    subgraph svc["애플리케이션 — app/services 공유"]
        api["FastAPI<br/>app/api"]
        mcps["MCP Server<br/>FastMCP · stdio"]
    end

    proxy["OpenProxy<br/>VIP : 6432 — 단일 엔드포인트"]
    db[("OpenSQL v3<br/>PostgreSQL 17 + pgvector")]
    trg["AFTER 트리거<br/>같은 트랜잭션"]
    worker["Embedding Worker<br/>python -m app.worker"]
    model["BGE-M3 로컬 구동<br/>sentence-transformers"]

    ui -->|"업로드 · 텍스트 편집 · 버전 되돌리기 (세션 쿠키)"| api
    rest -->|"REST + Bearer 토큰"| api
    mcpc -->|"search_documents · create_document"| mcps

    api -->|"documents INSERT/UPDATE만 — 임베딩 호출 없음"| proxy
    api -->|"권한·태그 필터 + 벡터 유사도를 단일 SQL로"| proxy
    mcps -->|"HTTP 미경유 · 같은 services 재사용"| proxy

    proxy -->|"커넥션 풀링 · Primary 추적 · 재연결"| db
    db --> trg
    trg -->|"버전 이력 · embedding_jobs · NOTIFY"| db

    worker -->|"5초 폴링(주 경로) + FOR UPDATE SKIP LOCKED로 잡 점유"| proxy
    worker -->|"청크 배치 임베딩"| model
    worker -->|"해시 재확인 + 청크 교체 — 단일 트랜잭션"| proxy
    api -.->|"질의 임베딩"| model
```

**애플리케이션은 `documents`에 쓰기만 합니다.** 텍스트 버전 이력·임베딩 작업·알림은 AFTER 트리거가 **같은 트랜잭션에서** 만들고, 워커는 그 작업을 집어가는 무상태 실행기입니다. Web UI·REST·MCP는 같은 `app/services`를 소비하는 대등한 인터페이스이며, DB 접속은 OpenProxy 단일 엔드포인트만 거칩니다 ([ADR-006](docs/ADR.md)).

---

## 무엇을 해결하는가

문서 검색에 벡터 DB를 붙이면 흔히 이런 문제가 생깁니다.

1. **원본과 벡터가 어긋난다.** 문서를 고쳤는데 임베딩 갱신을 깜빡하거나, 별도 파이프라인이 실패하면 검색 결과가 옛날 내용을 가리킵니다.
2. **DB가 죽으면 서비스가 죽는다.** 단일 DB 장애가 곧 전체 장애입니다.

OpenArchive는 이 둘을 **DB 계층에서** 해결합니다.

- **정합성**: 문서를 수정하면 트리거가 **같은 트랜잭션 안에서** 재임베딩 작업을 만듭니다. 애플리케이션 코드에는 임베딩 호출이 없습니다. "문서만 저장되고 벡터 갱신은 유실되는" 상태가 구조적으로 불가능합니다.
- **장애 자동 복구**: [OpenSQL](https://docs.tibero.com/tmaxopensql/overview) 위에서 동작합니다. **DB 프로세스 장애 자동 복구를 검증했습니다** — Patroni가 감지해 스스로 재기동하고, 애플리케이션이 재연결하며, 미처리 임베딩 작업은 `embedding_jobs`에 남아 그대로 재개되고 정합성 카운터가 0으로 수렴합니다. **노드 사망은 복구되지 않으며, 이는 사무국 지시에 따른 Single 구성의 제약입니다.** HA 설계는 유지하되 노드 승격은 검증하지 못했습니다 ([ADR-020](docs/ADR.md)).

### 무엇을 보장하고, 무엇을 보장하지 않는가

즉시 반영을 약속하지 않습니다. 재임베딩 중에는 이전 버전 벡터로 검색되고, 워커 폴링 주기(5초)와 임베딩 소요만큼 반영이 늦으며, 장애 복구 구간에는 요청이 실패합니다.

| 보장한다 | 보장하지 않는다 |
|---|---|
| **버전 일관성** — 검색되는 청크는 어느 시점에도 하나의 버전이며 섞이지 않습니다 | 즉시 반영 |
| **최신으로 수렴** — 지연은 있어도 결국 최신 버전에 도달합니다 | 요청 실패 없는 복구 |
| **관측 가능성** — 어긋난 구간을 쿼리 한 줄로 셀 수 있습니다 | |

```sql
-- 원본과 벡터가 어긋난 문서. 파이프라인이 정상이면 0으로 수렴한다.
SELECT count(*) FROM documents d
  JOIN document_chunks c ON c.document_id = d.id
 WHERE c.version <> d.version;
```

문서를 고치면 이 값이 잠깐 올랐다가 워커 처리 후 0으로 돌아옵니다. 장애를 일으켜도 결국 0으로 수렴합니다. `scripts/demo_recovery.sh`는 DB 프로세스가 죽어도 Patroni가 스스로 재기동하고, 앱이 재연결해 미처리 잡을 이어 처리하며, 정합성 카운터가 0으로 수렴하는지 실제로 검증합니다. **증명할 수 있는 주장을 하는 것**이 과장된 최신성 표현보다 낫다고 판단했습니다 ([ADR-015](docs/ADR.md)).

워커 프로세스가 강제 종료되는 경우도 같은 원리로 복구됩니다. 배포 호스트에서는 systemd 유닛이 워커를 되살리고, 재기동한 워커가 방치된 잡을 회수합니다. 그 회수를 기다리는 동안에도 나머지 잡은 계속 처리되며, 워커를 반복적으로 죽이는 잡은 재시도 예산을 소진한 뒤 `error`로 격리되어 파이프라인을 막지 않습니다 ([ADR-038](docs/ADR.md)).

---

## 동작 방식

```
문서 업로드 — Web UI · REST API · documents에 INSERT하는 모든 클라이언트
   │
   ▼
documents 테이블 INSERT/UPDATE
   │
   ├─ AFTER 트리거 (같은 트랜잭션)
   │     ├─ 버전 이력 기록
   │     ├─ 임베딩 작업 생성  ← 트랜잭셔널 아웃박스
   │     └─ 워커 깨우기 (NOTIFY)
   ▼
Embedding Worker
   │  SKIP LOCKED로 작업 선점 → 청킹 → 임베딩
   │  커밋 직전 content_hash 재확인 (낡은 결과 폐기)
   ▼
document_chunks 교체 (단일 트랜잭션)
   │
   ▼
하이브리드 검색 — 태그·유형·권한 필터 + 벡터 유사도를 단일 SQL로
   │
   ▼
근거 소비·문서 공급 — Web UI · REST API · MCP (같은 services 계층)
```

**핵심은 "애플리케이션이 임베딩 파이프라인을 조율하지 않는다"는 점입니다.** 업로드 API는 `INSERT`만 합니다. 나머지는 DB가 합니다. 그래서 문서를 공급하는 주체가 꼭 사람일 필요가 없습니다 — `documents`에 INSERT하는 클라이언트는 무엇이든 같은 파이프라인을 얻습니다 ([Roadmap](docs/ROADMAP.md)).

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| 자동 임베딩 파이프라인 | PDF·DOCX·TXT·MD 업로드 시 트리거가 작업 생성, 워커가 청킹·임베딩·저장 |
| 하이브리드 검색 | 정형 필터(태그·유형·권한)와 벡터 유사도를 **하나의 SQL**로 결합 |
| 텍스트 버전 관리·자동 재임베딩 | Web UI에서 **추출 텍스트를 직접 편집**. 수정하면 이력이 쌓이고 재임베딩이 자동 기동. 처리 중에는 이전 벡터로 검색이 계속됨. 과거 버전의 본문을 펼쳐 보고 **되돌리면 이력을 되감지 않고 새 텍스트 버전이 생김** ([ADR-037](docs/ADR.md)) |
| 관련 문서·태그 추천 | 임베딩 완료 시 저장된 관계(edge)로 유사 문서를 찾고, 그 문서들의 태그를 추천. 검색과 동일한 권한 필터 적용 |
| 장애 자동 복구 | **DB 프로세스** 장애 시 자동 재기동과 앱 재연결, 미처리 작업 무손실 재개. 노드 승격은 검증하지 않았습니다(Single 구성) |
| MCP 근거 게이트웨이 | AI 에이전트에 발췌·출처·기준 버전을 공급하고 `txt`·`md` 문서 텍스트를 생성. 답변 생성은 클라이언트가 수행 |
| 위임 API 토큰 | 계정 설정 화면에서 프로그램용 토큰을 직접 발급·폐기. scope는 `read`·`read_write` 둘이고 원문은 발급 직후 한 번만 보임 ([ADR-034](docs/ADR.md)·[040](docs/ADR.md)) |

> **원본 파일은 보관하지 않습니다.** 업로드된 PDF·DOCX에서 텍스트만 추출해 저장하며, 편집·버전 관리·임베딩의 대상은 그 **추출 텍스트**입니다. 원본 파일 다운로드는 지원하지 않습니다 ([ADR-017](docs/ADR.md)).

---

## 기술 스택

| 영역 | 사용 기술 |
|---|---|
| DB | [Tmax OpenSQL v3](https://docs.tibero.com/tmaxopensql/overview) (PostgreSQL 17 + pgvector) · OpenHA(Patroni) · OpenHA DCS(etcd) · OpenProxy |
| 백엔드 | Python 3.12+ · FastAPI · psycopg3 |
| 임베딩 | [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) (MIT, 1024차원) — 로컬 구동 |
| 프론트엔드 | Next.js (App Router) · TypeScript · Tailwind CSS · [JSZip 3.10.1](https://stuk.github.io/jszip/) (MIT) |
| MCP | Python `mcp` SDK (FastMCP, stdio) |

---

## 빠른 시작

### OpenSQL 라이선스는 필요하지 않습니다

**아래 절차는 OpenSQL 없이 그대로 완주합니다.** 로컬 DB는 `pgvector/pgvector:pg17`
컨테이너 한 대이고, 스키마·트리거·검색 SQL은 전부 표준 PostgreSQL 17 기능이라 OpenSQL
전용 확장에 의존하지 않습니다. 애플리케이션이 OpenSQL과 만나는 지점은 `DATABASE_URL`
환경변수 하나뿐입니다 ([ADR-006](docs/ADR.md), [ADR-007](docs/ADR.md)).

| 로컬 컨테이너로 되는 것 | 실 OpenSQL 환경이 필요한 것 |
|---|---|
| 자동 임베딩 파이프라인 전 구간 — 트리거·아웃박스·`SKIP LOCKED`·청킹·임베딩 | OpenProxy 경유 세션 동작 ([ADR-009](docs/ADR.md)) |
| 하이브리드 검색 · 관계 그래프 · 태그 추천 | 읽기/쓰기 분리와 복제 지연 ([ADR-010](docs/ADR.md)) |
| 텍스트 버전·되돌리기, 권한 모델, API 토큰 | Patroni 리더 선출·승격 |
| Web UI · REST API · MCP 서버 전체 | 장애 복구 데모 (`scripts/demo_recovery.sh`) |
| `bash scripts/check.sh` 전체 통과 | 라이선스·번들 확장 실동작 |

OpenSQL 자체를 세우려면 [OpenSQL 환경 구축](docs/SETUP_OPENSQL.md)을 따르십시오 — 설치
파일과 라이선스가 따로 필요하고, x86-64 + Rocky Linux 9.7 전용입니다.

### 준비물
- Docker · Docker Compose
- Python 3.12+
- Node.js 20+

### 실행

```bash
# 1. 로컬 DB (pgvector 컨테이너)
docker compose up -d

# 2. 백엔드 설치
cd backend
python3 -m venv .venv && source .venv/bin/activate   # scripts/check.sh가 backend/.venv를 찾는다
pip install -e ".[dev]"          # 기본은 가짜 임베딩이다. 실제 BGE-M3는 아래 「임베딩 프로바이더」

# 3. DB 준비 — 연결 확인·확장 점검·스키마 적용·준비 상태 확인을 한 번에 한다
openarchive init                 # 아래 「openarchive init」 참조

# 4. 최초 관리자 계정 — 자체 가입이 없으므로 첫 로그인 전에 한 번 실행한다
cd .. && ADMIN_PASSWORD='<초기 비밀번호>' python scripts/create_admin.py admin --admin
cd backend

# 5. API + 임베딩 워커 + 웹 화면 — 한 명령이 전부 띄운다
openarchive serve                # 아래 「openarchive serve」 참조

# 6. MCP 서버 (Claude Desktop/Code가 기동한다. 수동 확인은 아래 커맨드)
#    MCP_USER_ID는 4번에서 만든 계정명과 같아야 한다 — 아래 「MCP 서버 등록」 참조.
MCP_USER_ID=admin python -m mcp_server.server
```

`http://localhost:8000` 접속. **웹 화면은 API와 같은 주소에서 나옵니다** — 빌드된
프론트가 백엔드 패키지에 동봉돼 있어 Node.js가 필요하지 않습니다 ([ADR-041](docs/ADR.md)).
프론트 코드를 고칠 때만 아래 「프론트엔드를 고칠 때」를 보십시오.

### `openarchive init`

도입할 DB를 준비 상태로 만드는 명령입니다. 하는 일은 넷입니다 — **연결 확인 → capability
확인(PostgreSQL 버전·`vector`·`pg_trgm`·CREATE 권한) → 마이그레이션 적용 → 준비 상태 보고**.
확인이 적용보다 먼저이므로, 확장이 없거나 권한이 모자라면 스키마를 건드리기 전에 무엇이
왜 필요한지 알려주고 멈춥니다.

```bash
openarchive init                                   # 대화형 — DSN 한 줄만 입력
openarchive init --dsn "postgresql://app@<vip>:6432/<pool_name>" --yes   # 비대화형
```

DSN을 확인한 뒤 `backend/.env`의 `DATABASE_URL` 줄만 갈아 끼웁니다. 다른 설정은 보존됩니다.

> **기존 데이터베이스를 덮어쓰지 않습니다.** `schema_migrations`가 없는데 OpenArchive가 쓰는
> 테이블 이름(`documents`·`users` 등)이 이미 있으면 **아무것도 바꾸지 않고 중단**합니다.
> 마이그레이션에는 `ALTER TABLE documents`가 있어, 같은 이름의 다른 테이블 위에서 돌면 그
> 데이터가 손상되기 때문입니다 ([ADR-039](docs/ADR.md)).

**하지 않는 것**: API·워커·프론트 기동, DB 자동 탐색·설치, 문서 공급, 계정 생성. 마지막에 다음
단계를 출력만 합니다. 이 명령을 건너뛰어도 API 서버가 startup에서 같은 마이그레이션을
적용하므로([ADR-012](docs/ADR.md)), init은 **필수가 아니라 사전 점검**입니다.

`openarchive`의 다른 하위 명령은 `reset-password` 하나이며, 비밀번호를 잊은 계정을 여는 데 씁니다
(아래 「인증 환경변수」).

### `openarchive serve`

API 서버와 임베딩 워커를 함께 띄웁니다. **워커를 따로 켜는 것을 잊을 수 없게 하는 것**이
목적입니다 — 워커가 없으면 업로드는 성공하는데 검색에 잡히지 않고, 에러가 나지 않아
원인을 짐작하기 어렵습니다.

```bash
openarchive serve                              # 127.0.0.1:8000
openarchive serve --host 0.0.0.0 --port 9000
```

- 웹 화면·API·워커가 한 주소에서 나옵니다. 별도 프론트 서버가 필요 없습니다.
- Ctrl-C 한 번에 둘 다 멈춥니다. 워커는 **처리 중인 잡을 마치고** 종료합니다.
- 한쪽이 멈추면 나머지도 내리고 0이 아닌 코드로 끝납니다 — 반쪽만 도는 상태를 만들지 않습니다.
- **죽은 프로세스를 되살리지는 않습니다.** 배포 호스트에서는 systemd가 그 역할을 합니다
  ([ADR-038](docs/ADR.md) · `scripts/openarchive-worker.service`).

코드를 고치며 개발할 때는 자동 재시작(`--reload`)이 필요하므로 두 프로세스를 따로 띄웁니다.

```bash
uvicorn app.main:app --reload                  # 터미널 1
python -m app.worker                           # 터미널 2
```

### 프론트엔드를 고칠 때

웹 화면은 `backend/app/static/`에 동봉된 빌드 산출물입니다(1.5MB). 프론트 소스를 고쳤다면
**다시 빌드해 산출물을 갱신하고 함께 커밋해야** 화면에 반영됩니다.

```bash
cd frontend
npm install
npm run dev            # 개발 서버 — http://localhost:3000, /api/*는 8000으로 프록시된다
npm run build:static   # 정적 export + backend/app/static 갱신
```

`bash scripts/check.sh`가 `build:static`을 돌리므로, 검증만 통과시켜도 산출물이 최신이
됩니다 — 갱신분은 `git diff`에 드러납니다.


### 환경변수 파일은 `backend/.env` 하나입니다

기본값만으로 위 절차가 완주하므로 `.env`는 **설정을 바꿀 때만** 만듭니다. 만들 때 위치는
`backend/` 안입니다.

```bash
cd backend && cp .env.example .env
```

애플리케이션 설정을 읽는 주체는 API·워커·MCP 서버와 `scripts/create_admin.py` 넷인데 실행
디렉토리가 서로 다릅니다. `app/config.py`가 `backend/.env` 한 곳만 절대경로로 읽어 넷이 같은
값을 보게 합니다. 환경변수를 직접 주는 방식(`DATABASE_URL=... uvicorn ...`)은 언제나 파일보다
우선합니다.

> **저장소 루트의 `.env`는 다른 파일입니다.** 애플리케이션은 그 파일을 읽지 않지만
> **`docker compose`가 읽습니다** — `docker-compose.yml`의 `${POSTGRES_USER:-openarchive}`
> 세 자리를 채우는 것이 그 파일입니다. 로컬 DB의 자격증명·DB 이름을 바꾸려면 루트 `.env`에
> `POSTGRES_*`를 두고, 앱이 붙을 주소는 `backend/.env`의 `DATABASE_URL`에 둡니다. 둘은 역할이
> 다르므로 한쪽에 몰아 쓰면 컨테이너와 앱이 서로 다른 DB를 가리킵니다.

### 임베딩 프로바이더

**기본값은 `fake`입니다.** 의존성이 가볍고 테스트가 빨라 개발·검증 기본값으로 둔 것인데, 이
상태에서도 업로드·검색이 **동작하기 때문에** 알아채기 어렵습니다. 가짜 벡터는 의미를 담지
않으므로 **검색 품질을 이 상태로 판단하지 마세요.**

실제 `BAAI/bge-m3`로 돌리려면 설치와 환경변수가 **둘 다** 필요합니다.

```bash
pip install -e ".[dev,local]"                      # sentence-transformers + torch (수 GB)

EMBEDDING_PROVIDER=local uvicorn app.main:app --reload   # API — 검색 질의를 임베딩한다
EMBEDDING_PROVIDER=local python -m app.worker            # 워커 — 문서를 임베딩한다
```

| 환경변수 | 기본값 | 값 |
|---|---:|---|
| `EMBEDDING_PROVIDER` | `fake` | `local` — `BAAI/bge-m3`(MIT, 1024차원) · `fake` — 테스트용 |

**API·워커·MCP 서버는 각자 프로바이더를 생성하므로 세 프로세스에 같은 값을 주어야 합니다.**
값이 엇갈리면 질의 벡터와 문서 벡터가 다른 공간에 놓여, 에러 없이 검색 결과만 무의미해집니다.
모델은 **API·워커가 기동할 때** 내려받아 캐시하므로 최초 1회는 기동이 오래 걸립니다(ADR-003 보강). 대신 첫 검색·첫 업로드가 로딩을 기다리지 않습니다.

### 인증 환경변수

| 환경변수 | 기본값 | 설명 |
|---|---:|---|
| `SESSION_LIFETIME_HOURS` | `24` | 서버 세션과 로그인 쿠키의 수명(시간) |
| `SESSION_COOKIE_SECURE` | `false` | 로컬 HTTP에서는 `false`. HTTPS 상시 배포에서는 반드시 `true` |

초기 계정은 환경변수로 자동 생성되지 않는다. API 서버를 먼저 기동해 마이그레이션을 적용한 뒤 위의
`scripts/create_admin.py`를 실행한다. 비밀번호를 셸 기록에 남기고 싶지 않으면 `ADMIN_PASSWORD`를
생략하면 대화형으로 입력받는다. 이후 관리자는 `/admin/users`에서 일반 사용자나 다른 관리자를
발급할 수 있다. 관리자 권한은 계정 관리 전용이며 다른 사용자의 private 문서를 열람하게 하지는 않는다.

각 사용자는 헤더의 사용자명을 눌러 **계정 설정**(`/settings`)에서 자기 비밀번호를 바꾸고 API
토큰을 발급·폐기한다. 비밀번호를 바꾸면 그 계정으로 열려 있던 모든 기기의 로그인이 끊기고
다시 로그인해야 한다. 발급한 API 토큰은 영향을 받지 않는다 ([ADR-040](docs/ADR.md)).

**비밀번호를 잊어 로그인조차 못 하는 계정**은 운영자가 서버에서 재설정한다. 관리 화면에는 남의
비밀번호를 바꾸는 경로를 두지 않는다 — 관리자가 남의 계정을 탈취해 그 사람의 private 문서를 읽게
되면 "관리자 권한은 계정 관리 전용"이라는 경계가 무너지기 때문이다.

```bash
cd backend && source .venv/bin/activate
openarchive reset-password alice     # 새 비밀번호는 화면에 남지 않게 입력받는다
```

재설정하면 그 계정의 로그인 세션이 모두 끊긴다. 발급된 API 토큰은 그대로 유효하므로, 자격증명까지
갈아야 하면 다시 로그인해 `/settings`에서 폐기한다.

### Claude Code/Desktop에 MCP 서버 등록

stdio 서버 설정에 백엔드 가상환경의 Python과 모듈을 등록한다. `<REPOSITORY>`는 이 저장소의 절대 경로로 바꾼다.

```json
{
  "mcpServers": {
    "openarchive": {
      "command": "<REPOSITORY>/backend/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "DATABASE_URL": "postgresql://openarchive:openarchive@localhost:5433/openarchive",
        "EMBEDDING_PROVIDER": "fake",
        "MCP_USER_ID": "admin"
      }
    }
  }
}
```

등록되는 도구는 `search_documents`, `get_document`, `list_documents`, `create_document` 네 개다.
`DATABASE_URL`, `EMBEDDING_PROVIDER`, `MCP_USER_ID`를 MCP 프로세스에 함께 전달해야 한다.
`MCP_USER_ID`를 생략하면 public 문서 읽기만 가능하고 `create_document` 쓰기는 거부된다. MCP 서버는
마이그레이션을 실행하지 않으므로 API 서버를 먼저 기동해 스키마가 적용된 상태여야 한다 (ADR-012·036).

> ⚠️ **`MCP_USER_ID`는 실존 계정인지 검증되지 않는다.** 미설정·빈 값·공백은 거부되지만,
> 그 검사를 통과한 이름은 users 테이블에 없어도 그대로 문서 소유자가 된다 (ADR-036). 위 3번에서 만든 계정명과 **정확히 같게** 적어야
> `create_document`로 만든 문서가 Web UI에서 자기 문서로 보인다.

### API 확인

엔드포인트 목록과 요청·응답 스키마는 서버가 직접 제공합니다. 구현과 어긋날 수 없는 유일한 출처입니다.

> **문서 관련 API는 읽기까지 전부 인증을 요구합니다** — 익명 요청은 401입니다 (ADR-028·034).
> 사람은 위 3번에서 만든 계정으로 `POST /api/auth/login`을 호출하고, 프로그램은 위임 API 토큰을
> Bearer로 보냅니다. MCP 서버는 HTTP를 거치지
> 않고 서비스를 직접 호출하므로 이 경계와 무관하며, 열람 범위는 `MCP_USER_ID`가 정합니다.

```
http://localhost:8000/docs
```

사람이 사용하는 클라이언트는 `POST /api/auth/login` 뒤 세션 쿠키를 동봉합니다. 프로그램은 로그인
세션에서 발급한 API 토큰을 Bearer 자격증명으로 씁니다. 발급은 웹의 **계정 설정**(`/settings`)에서
하며, `POST /api/auth/tokens`를 직접 호출해도 됩니다.
기본 scope는 `read`이며 문서 공급에는 `read_write`가 필요합니다. 원문 토큰은 발급 응답에만 나오고,
`GET /api/auth/tokens` 목록에는 다시 나타나지 않습니다. 발급·목록·폐기와 `/api/admin/*`는 세션
전용입니다.

독립 예제는 `backend` 패키지를 import하지 않고 토큰만으로 텍스트 공급과 상태 폴링을 수행합니다.

```bash
python3 examples/ingest_text.py \
  --base-url http://localhost:8000 \
  --token "$OPENARCHIVE_API_TOKEN" \
  --title "API 문서" \
  document.md
```

예제의 실제 서버 완주는 CI가 확인하지 않으므로 API·워커·DB를 함께 기동한 환경에서 실행해야 합니다.
첫 커넥터와 MCP update/delete·원격 transport는 [Roadmap](docs/ROADMAP.md)의 다음 작업으로 남아 있습니다.

설계 의도와 각 엔드포인트의 근거는 [Architecture](docs/ARCHITECTURE.md#api-설계)에 있습니다.

### 검증

```bash
bash scripts/check.sh    # 백엔드 lint+test, 프론트엔드 lint+test+build
```

복구 데모에는 마이그레이션이 적용된 **실 OpenSQL VM**과 `backend/.venv`의 개발 의존성이 필요합니다. 로컬 Docker DB에서는 실행할 수 없습니다. 스크립트가 API와 워커를 직접 띄우므로 별도로 실행해 둘 필요는 없습니다.

```bash
# 기본값은 OPENSQL_HOST=192.168.64.4, OPENSQL_SSH=$OPENSQL_HOST,
# PATRONI_URL=http://$OPENSQL_HOST:8008,
# PATRONI_LOG=/home/opensql/logs/patroni.log, API_PORT=18000이다.
OPENSQL_HOST=<vm-ip> \
OPENSQL_SSH=<ssh-host> \
DATABASE_URL="postgresql://postgres:pg_password@<vm-ip>:6432/opensql" \
PATRONI_URL="http://<vm-ip>:8008" \
PATRONI_LOG="/home/opensql/logs/patroni.log" \
API_PORT=18000 \
bash scripts/demo_recovery.sh
```

SSH 공개키 인증과 원격 호스트의 비밀번호 없는 `sudo`가 필요합니다. 데모는 postmaster 부모 프로세스에 `SIGKILL`을 한 번 보내고 Patroni의 자동 재기동, 앱 연결 예외와 재접속, 미처리 잡 재개, 정합성 수렴을 단일 타임라인으로 확인합니다.

**DB 프로세스 장애 자동 복구를 검증했다. 노드 사망은 복구되지 않으며, 이는 사무국 지시에 따른 Single 구성의 제약이다. HA 설계는 유지하되 노드 승격은 검증하지 못했고, 애플리케이션 측 재연결·잡 재개·정합성 수렴을 함께 검증했다.**

### 실 OpenSQL 클러스터에 연결

애플리케이션은 OpenProxy VIP 단일 엔드포인트만 바라봅니다. 코드 변경 없이 환경변수만
바꾸면 됩니다.

```bash
DATABASE_URL="postgresql://app@<vip>:6432/<pool_name>"
```

> ⚠️ **DSN을 바꾸기 전에 OpenProxy 풀이 어느 데이터베이스를 바라보는지 확인하십시오.**
> 설치기는 `opensql` 데이터베이스를 만들어놓고 정작 풀은 관리용 `postgres`를 바라보게
> 설정합니다. 클라이언트는 DSN에 **풀 이름**을 적으므로 실제 저장 위치가 드러나지 않아,
> 그대로 두면 마이그레이션과 문서가 `postgres`에 쌓입니다. 교정 절차는
> [OpenSQL 환경 구축](docs/SETUP_OPENSQL.md)의 §10 「풀이 바라보는 데이터베이스를
> 교정한다」에 있습니다.

---

## 문서

| 문서 | 내용 |
|---|---|
| [PRD](docs/PRD.md) | 제품 요구사항, MVP 범위 |
| [Roadmap](docs/ROADMAP.md) | 확장점 지도와 단계적 발전 경로 — 지금 하지 않는 것과 그 이유 |
| [Architecture](docs/ARCHITECTURE.md) | 스키마·트리거·워커·검색·고가용성 상세 |
| [ADR](docs/ADR.md) | 설계 결정과 각각의 근거·트레이드오프 |
| [OpenSQL 조사](docs/OPENSQL_RESEARCH.md) | 배포판 확정 사항, 공식 문서 조사 결과, 검증 계획 |
| [OpenSQL 환경 구축](docs/SETUP_OPENSQL.md) | Rocky Linux 9.7 VM 준비부터 single 모드 설치·검증까지 |
| [UI Guide](docs/UI_GUIDE.md) | 디자인 원칙, 화면 구성 |
| [Contributing](CONTRIBUTING.md) | 개발 규약, 브랜치·커밋 컨벤션 |

설계 결정에 의문이 생기면 [ADR](docs/ADR.md)을 보십시오. 왜 그렇게 했는지, 무엇을 포기했는지가 적혀 있습니다.

---

## AI 모델 활용

이 프로젝트는 문서 임베딩 생성에 **공개 가중치 모델을 로컬에서 구동**합니다. 외부 API 전용 모델은 사용하지 않습니다.

| 항목 | 내용 |
|---|---|
| 모델 | [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) |
| 개발사 | Beijing Academy of Artificial Intelligence (BAAI) |
| 라이선스 | MIT |
| 활용 방식 | 사전학습 가중치를 추가 학습 없이 그대로 사용 (외부 모델 그대로 활용) |
| 구동 환경 | 로컬 — `sentence-transformers`. 외부 API 호출 없음 |

---

## 라이선스

[MIT License](LICENSE)

의존하는 오픈소스의 출처와 라이선스는 SBOM으로 함께 공개합니다.
