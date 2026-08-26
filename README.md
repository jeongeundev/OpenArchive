# OpenArchive

**문서를 고치면 DB가 벡터를 따라 맞추는, AI를 위한 문서관리 플랫폼**

[![CI](https://github.com/jeongeundev/OpenArchive/actions/workflows/ci.yml/badge.svg)](https://github.com/jeongeundev/OpenArchive/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PostgreSQL](https://img.shields.io/badge/OpenSQL-PostgreSQL%2017-336791.svg)](https://docs.tibero.com/tmaxopensql/overview)
[![Embedding](https://img.shields.io/badge/embedding-BGE--M3%20(MIT)-orange.svg)](https://huggingface.co/BAAI/bge-m3)

> 2026년 오픈소스 개발자대회 기업 지정과제 「OpenSQL 기반 AI 문서관리 플랫폼」 출품작

---

## 소개

OpenArchive는 조직의 문서를 올리면 **텍스트 추출 → 청킹 → 임베딩 → 벡터 저장 → 문서 관계
생성**이 자동으로 일어나고, 정형 필터(태그·유형·권한)와 벡터 유사도와 문서 관계를 **하나의
SQL**로 결합해 검색하는 문서관리 플랫폼입니다. 사람은 웹 화면으로, 프로그램은 REST API로,
AI 에이전트는 MCP로 같은 문서와 같은 권한 규칙 위에서 접근합니다.

[Tmax OpenSQL](https://docs.tibero.com/tmaxopensql/overview)(PostgreSQL 17 + pgvector) 위에서
동작하며, 기업 과제가 제시한 두 문제를 **DB 계층에서** 풉니다.

- **원본과 벡터의 정합성** — 문서를 저장하면 DB 트리거가 **같은 트랜잭션 안에서** 임베딩
  작업·버전 이력·문서 관계를 만듭니다. 애플리케이션 코드에는 임베딩 호출이 없고, "문서만
  저장되고 벡터 갱신은 유실되는" 상태가 구조적으로 존재하지 않습니다.
- **장애 자동 복구** — 장애 감지·재기동·재연결은 OpenSQL(Patroni·OpenProxy)이 맡고,
  애플리케이션은 단일 엔드포인트로만 접속합니다. 미처리 작업은 DB 테이블에 남아 복구 뒤
  그대로 재개됩니다.

보장 범위는 **버전 일관성 + 최신 수렴**입니다 — 검색되는 청크는 어느 시점에도 하나의 텍스트
버전이며, 변경은 유한 시간 안에 최신으로 수렴합니다. 즉시 반영은 약속하지 않습니다
([ADR-015](docs/ADR.md)).

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| 자동 임베딩 파이프라인 | PDF·DOCX·TXT·MD 업로드(ZIP 일괄 포함) 시 트리거가 작업을 만들고 워커가 청킹·임베딩·저장. **원본 파일은 보관하지 않고 추출 텍스트만 남음** |
| 하이브리드 검색 | 태그·유형·권한 필터 + 벡터 유사도 + 저장된 문서 관계(깊이 2)를 **단일 SQL**로 결합. 결과에 "왜 나왔는지"를 함께 표시 |
| 텍스트 버전 관리 | 추출 텍스트를 직접 편집하면 이력이 쌓이고 재임베딩이 자동 기동. 처리 중에는 이전 벡터로 검색이 계속됨. 되돌리기는 새 버전을 만듦 |
| 자동 정리 | 관계 그래프의 Louvain 군집으로 주제 덩어리를 태그 없이 묶고, 관련 문서·태그 추천, 고아·중복·깨진 위키링크 진단 |
| 권한 | 볼 수 없는 문서는 검색·관련 문서·그래프·집계 어디에도 나타나지 않음. 웹·REST·MCP가 같은 열람 규칙을 공유 |
| MCP 근거 게이트웨이 | AI 에이전트에 발췌·출처·기준 버전을 공급하고(`search_documents` 등), `create_document`로 문서를 공급받음. 답변 생성은 클라이언트가 수행 |
| 위임 API 토큰 | 계정 설정에서 프로그램용 토큰을 발급·폐기. scope는 `read`·`read_write` |
| 장애 자동 복구 | DB 프로세스 장애 시 자동 재기동·앱 재연결·미처리 작업 무손실 재개를 실 OpenSQL에서 검증 |

---

## 시작하기

필요한 것은 Git, Python 3.12+, 그리고 OpenSQL입니다. 배포 패키지는 없으므로 소스에서
설치합니다.

아래 블록에서 **DSN 한 줄만 자기 환경으로 바꿔** 붙여넣습니다 — `<OpenProxy 호스트>`와
`<풀 이름>`을 실제 값으로 바꾸지 않으면 그 문자열이 그대로 호스트명이 되어 연결에 실패합니다.
OpenProxy 경유라면 데이터베이스 자리에 **풀 이름**을 적습니다.

```bash
git clone https://github.com/jeongeundev/OpenArchive.git
cd OpenArchive/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[local]"
openarchive init --yes --dsn "postgresql://app:secret@<OpenProxy 호스트>:6432/<풀 이름>"   # ← 이 줄만 바꾼다
ADMIN_PASSWORD='change-me' python ../scripts/create_admin.py admin --admin
EMBEDDING_PROVIDER=local openarchive serve
```

브라우저에서 http://localhost:8000 을 열고 `admin` / `change-me`로 로그인합니다. 비밀번호는 설정
화면에서 바로 바꿉니다.

> ⚠️ 설치기는 `opensql` 데이터베이스를 만들어 놓고 OpenProxy 풀은 관리용 `postgres`를 바라보게
> 설정합니다. DSN을 주기 전에 풀이 어느 DB를 가리키는지 확인하세요 —
> [OpenSQL 환경 구축 §10](docs/SETUP_OPENSQL.md#10-설치-확인). OpenSQL 자체를 세우는 절차도 그
> 문서에 있습니다.

### 각 단계가 하는 일

- **`pip install -e ".[local]"`** — `local`은 BGE-M3 임베딩 모델입니다 (torch 포함, 수 GB). 동작만
  빠르게 확인하려면 `".[dev]"`로 설치하고 `EMBEDDING_PROVIDER=local`을 빼도 됩니다 — 가짜 벡터로
  파이프라인 전 구간이 돌지만 **검색 결과에 의미가 없으므로** 실제 사용에는 `local`이 필요합니다.
- **`openarchive init`** — **연결 확인 → 확장·권한 점검 → 스키마 적용 → 준비 상태 보고**를 한 번에
  합니다. 확인이 적용보다 먼저라 `vector` 확장이 없거나 권한이 모자라면 스키마를 건드리기 전에 무엇이
  왜 필요한지 알려주고 멈추고, 기존 테이블이 있으면 아무것도 바꾸지 않습니다
  ([ADR-039](docs/ADR.md)). `--yes`를 빼면 단계마다 묻습니다. 확인한 DSN은 `backend/.env`에 기록됩니다.
- **`create_admin.py`** — 자체 가입이 없으므로 최초 관리자 계정을 한 번 만듭니다.
- **`openarchive serve`** — API + 임베딩 워커 + 웹 화면을 한 명령으로 띄웁니다. 웹 화면은 백엔드에
  동봉된 정적 빌드라 Node.js가 필요 없습니다. **최초 1회는 모델 다운로드(약 2GB)로 기동이 오래
  걸립니다.**

### 로컬 개발·평가용 대체 환경

OpenSQL 환경이 없으면 `pgvector/pgvector:pg17` Docker 컨테이너를 DB로 쓸 수 있습니다.
OpenArchive는 표준 PostgreSQL 17 + pgvector 인터페이스에 의존하므로 **동일한 애플리케이션
코드가 그대로 동작합니다.** 단, OpenHA(Patroni)의 장애 복구와 OpenProxy의 커넥션 풀링은
실 OpenSQL 환경에서만 검증할 수 있습니다 ([ADR-006](docs/ADR.md)).

<details>
<summary>Docker로 DB만 띄우는 명령 — 바꿀 줄이 없습니다</summary>

```bash
git clone https://github.com/jeongeundev/OpenArchive.git
cd OpenArchive
docker compose up -d --wait
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[local]"
openarchive init --yes --dsn "postgresql://openarchive:openarchive@localhost:5433/openarchive"
ADMIN_PASSWORD='change-me' python ../scripts/create_admin.py admin --admin
EMBEDDING_PROVIDER=local openarchive serve
```

</details>

---

## 사용 방법

### 문서 올리기

로그인 후 첫 화면(`/`)의 드롭존에 **PDF·DOCX·TXT·MD 파일이나 ZIP**을 끌어다 놓습니다
(파일당 10MB). 올린 문서는 목록에 **임베딩 중**으로 나타나고, 워커가 텍스트 추출·청킹·임베딩을
마치면 완료로 바뀝니다 — 보통 문서 하나에 몇 초입니다. 원본 파일은 보관하지 않으며 추출된
텍스트만 저장됩니다.

프로그램에서 올리려면 `/settings`에서 발급한 API 토큰(`read_write` scope)을 씁니다.

```bash
# 파일 업로드
curl -X POST http://localhost:8000/api/documents \
  -H "Authorization: Bearer $OPENARCHIVE_API_TOKEN" \
  -F "file=@guide.pdf" -F "title=설치 가이드" -F "tags=opensql" -F "visibility=private"

# 텍스트 직접 공급 — 임베딩이 끝날 때까지 폴링하는 독립 예제 (백엔드 패키지 불필요)
python3 examples/ingest_text.py notes.md --base-url http://localhost:8000 \
  --token "$OPENARCHIVE_API_TOKEN" --title "회의록 8/27" --tags 회의
```

`visibility`는 `public`(로그인한 모두) 또는 `private`(나만)입니다. 볼 수 없는 문서는 다른
사용자의 검색·관련 문서·그래프·집계 어디에도 나타나지 않습니다.

### 검색하기

`/search`에서 **검색어**를 입력하고, 필요하면 **태그**(쉼표 구분)·**문서 유형**·**결과 수**로
좁힙니다. 필터는 벡터 정렬 안쪽에서 적용되므로, 필터에 걸리거나 볼 수 없는 문서가 후보 자리를
차지하지 않습니다.

결과는 문서마다 발췌와 함께 **왜 나왔는지**를 보여줍니다 — 직접 맞은 결과는 `유사도 0.xxx`,
저장된 관계를 타고 온 결과는 *「설치 가이드」에서 「여러 대목에서 만난다」로 이어짐*처럼
출발 문서와 관계 종류가 붙습니다. 편집 직후에는 재임베딩이 끝날 때까지 이전 텍스트 버전으로
검색됩니다.

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Authorization: Bearer $OPENARCHIVE_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "OpenProxy 풀 설정", "tags": ["opensql"], "content_type": "pdf", "k": 10}'
```

### 문서 다듬기

문서 상세(`/documents/[id]`)에서 **추출 텍스트를 직접 편집**하면 텍스트 버전이 하나 쌓이고
재임베딩이 자동으로 기동합니다. 과거 버전을 펼쳐 보고 **되돌리기**를 누르면 이력을 되감는 것이
아니라 새 버전이 생겨 같은 파이프라인을 다시 탑니다. 같은 화면에서 **관련 문서**와 **태그
추천**(관련 문서들의 태그)을 보고 태그를 붙일 수 있습니다. 본문의 `[[제목]]`은 위키링크로
해석되어 백링크가 만들어집니다.

문서가 쌓이면 두 화면이 정리를 돕습니다.

- `/clusters` — 태그 없이도 **주제 덩어리**로 묶입니다 (저장된 관계 그래프의 Louvain 군집).
- `/diagnostics` — 고아 문서 · 동일 텍스트 · 깨진 위키링크.

### AI 에이전트 연결 (MCP)

Claude Desktop / Claude Code의 MCP 설정에 stdio 서버로 등록합니다. `<REPOSITORY>`는 이
저장소의 절대 경로, `DATABASE_URL`은 `backend/.env`와 같은 값입니다.

```json
{
  "mcpServers": {
    "openarchive": {
      "command": "<REPOSITORY>/backend/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "DATABASE_URL": "postgresql://openarchive:openarchive@localhost:5433/openarchive",
        "EMBEDDING_PROVIDER": "local",
        "MCP_USER_ID": "admin"
      }
    }
  }
}
```

에이전트는 `search_documents`로 발췌·출처·기준 버전을 받아 근거로 쓰고, `get_document` ·
`list_documents`로 읽으며, `create_document`로 문서를 공급합니다. 답변 생성은 에이전트 쪽입니다.
`MCP_USER_ID`가 열람 범위와 문서 소유자를 정하므로 **실제 계정명과 정확히 같게** 적습니다
(실존 여부는 검증되지 않습니다 — [ADR-036](docs/ADR.md)). `EMBEDDING_PROVIDER`는 `serve`에
준 값과 같아야 합니다 — 다르면 에러 없이 검색 결과만 무의미해집니다.

### 계정과 토큰

- `/settings` — 내 비밀번호 변경 · API 토큰 발급·폐기. 토큰 원문은 발급 직후 한 번만 보입니다.
- `/admin/users` · `/admin/status` — 사용자 발급 · 시스템 상태(임베딩 작업 카운터, 원본과 어긋난
  문서 수). 관리자 전용이며, 관리자라도 남의 비공개 문서는 볼 수 없습니다.
- `openarchive reset-password <user>` — 비밀번호를 잊은 계정을 서버에서 재설정. 웹에는 이 경로를
  두지 않습니다 ([ADR-040](docs/ADR.md)).

REST 전체 목록과 스키마는 `http://localhost:8000/docs`(OpenAPI)에 있습니다. 환경변수·프로세스
구성·복구 데모 등 운영 세부는 [운영 가이드](docs/OPERATIONS.md)를 보십시오.

---

## 아키텍처

이 아키텍처의 핵심은 **DB 안에 있는 것과 밖에 있는 것의 경계**입니다. 문서를 저장하면 트리거가
같은 트랜잭션에서 임베딩 작업·버전 이력·문서 관계를 만들고, 워커는 그것을 집어가는 무상태
실행기입니다. **DB 밖 연산은 임베딩 모델 추론 하나뿐이라**, 워커를 통째로 지워도 잡은 DB에 남고
다시 띄우면 이어서 처리합니다.

```mermaid
flowchart TB
    subgraph iface["소비·공급 인터페이스 — 대등한 3면"]
        direction LR
        ui["Web UI<br/>동봉 정적 빌드 · 세션 쿠키"]
        rest["REST API<br/>위임 토큰 (Bearer)"]
        mcps["MCP Server<br/>stdio · AI 에이전트"]
    end

    svc["backend/app/services<br/>모든 인터페이스가 공유하는 단일 진입점 · VISIBLE_TO_USER 술어"]
    proxy["OpenProxy — VIP:6432<br/>커넥션 풀 · Primary 추적 · 재연결"]

    subgraph db["OpenSQL v3 — PostgreSQL 17.8 + pgvector 0.8.1"]
        direction TB
        docs["documents"]
        trg1{{"AFTER 트리거<br/>같은 트랜잭션"}}
        ver["document_versions<br/>텍스트 버전 이력 (append-only)"]
        jobs["embedding_jobs<br/>트랜잭셔널 아웃박스 · 문서당 pending 1건"]
        links["document_links<br/>위키링크 해석"]
        chunks["document_chunks<br/>vector(1024) · HNSW"]
        trg2{{"AFTER 트리거<br/>청크 교체와 같은 트랜잭션"}}
        edges["document_edges<br/>overlaps · related (kNN, 순수 SQL)"]

        docs --> trg1
        trg1 --> ver
        trg1 --> jobs
        trg1 --> links
        chunks --> trg2 --> edges
    end

    worker["Embedding Worker — 무상태 실행기<br/>SKIP LOCKED claim → 청킹 → 임베딩 →<br/>해시 재확인 + 청크 교체 + job done (단일 트랜잭션)"]
    model["BGE-M3<br/>sentence-transformers 로컬 구동"]

    ui --> svc
    rest --> svc
    mcps --> svc
    svc -->|"documents INSERT/UPDATE만 — 임베딩 호출 없음<br/>필터 + 벡터 + 관계를 단일 SQL로"| proxy
    proxy --> db
    jobs -.->|"5초 폴링 (주 경로) + NOTIFY (최적화)"| worker
    worker -->|"청크 배치 임베딩"| model
    worker -->|"청크 교체 + ready 전이"| chunks
```

원본과 벡터가 어긋난 문서 수는 쿼리 한 줄로 셉니다. 문서를 고치면 잠깐 올랐다가 워커 처리 후
0으로 돌아오고, 장애를 일으켜도 결국 0으로 수렴합니다.

```sql
SELECT count(*) FROM documents d
  JOIN document_chunks c ON c.document_id = d.id
 WHERE c.version <> d.version;
```

이 그림을 떠받치는 설계 결정은 셋입니다 — 워커의 **기동 방식은 정합성의 일부가 아니고**(5초 폴링이
주 경로, `NOTIFY`는 최적화), **검색은 필터·벡터 유사도·관계 확장까지 단일 SQL**이며, **볼 수 없는
문서는 자리 표시조차 남기지 않습니다**. 각 결정의 근거와 트레이드오프는 [ADR](docs/ADR.md)에,
스키마·트리거·워커·검색 상세는 [Architecture](docs/ARCHITECTURE.md)에 있습니다.

### 기술 스택

| 영역 | 사용 기술 |
|---|---|
| DB | [Tmax OpenSQL v3](https://docs.tibero.com/tmaxopensql/overview) — PostgreSQL 17 + pgvector 0.8.1 · OpenHA(Patroni) · OpenHA DCS(etcd) · OpenProxy |
| 백엔드 | Python 3.12+ · FastAPI · psycopg3 · `openarchive` CLI |
| 임베딩 | [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) (MIT, 1024차원) — `sentence-transformers`로 로컬 구동 |
| 프론트엔드 | Next.js (App Router) · TypeScript · Tailwind CSS · JSZip — 정적 빌드를 백엔드에 동봉 |
| 군집 | `networkx` Louvain |
| MCP | Python `mcp` SDK (FastMCP, stdio) |

---

## 문서

| 문서 | 내용 |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | 스키마·트리거·워커·검색·고가용성 상세 |
| [ADR](docs/ADR.md) | 설계 결정과 각각의 근거·트레이드오프 — 재보고 물러난 결정 포함 |
| [PRD](docs/PRD.md) | 제품 요구사항, 하지 않는 것 |
| [Roadmap](docs/ROADMAP.md) | 확장점 지도와 단계적 발전 경로 |
| [운영 가이드](docs/OPERATIONS.md) | 환경변수, 프로세스 구성, 인증, 복구 데모 |
| [OpenSQL 조사](docs/OPENSQL_RESEARCH.md) | 배포판 확정 사항, 공식 문서 조사, 실측 기록 |
| [OpenSQL 환경 구축](docs/SETUP_OPENSQL.md) | Rocky Linux 9.7 VM 준비부터 설치·검증까지 |
| [UI Guide](docs/UI_GUIDE.md) | 디자인 원칙, 화면 구성 |
| [Contributing](CONTRIBUTING.md) | 개발 규약, 브랜치·커밋 컨벤션 |
