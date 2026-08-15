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
| 텍스트 버전 관리·자동 재임베딩 | Web UI에서 **추출 텍스트를 직접 편집**. 수정하면 이력이 쌓이고 재임베딩이 자동 기동. 처리 중에는 이전 벡터로 검색이 계속됨 |
| 관련 문서·태그 추천 | 임베딩 완료 시 저장된 관계(edge)로 유사 문서를 찾고, 그 문서들의 태그를 추천. 검색과 동일한 권한 필터 적용 |
| 장애 자동 복구 | **DB 프로세스** 장애 시 자동 재기동과 앱 재연결, 미처리 작업 무손실 재개. 노드 승격은 검증하지 않았습니다(Single 구성) |
| MCP 근거 게이트웨이 | AI 에이전트에 발췌·출처·기준 버전을 공급하고 `txt`·`md` 문서 텍스트를 생성. 답변 생성은 클라이언트가 수행 |

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

### 준비물
- Docker · Docker Compose
- Python 3.12+
- Node.js 20+

### 실행

```bash
# 1. 로컬 DB (pgvector 컨테이너)
docker compose up -d

# 2. 백엔드 — 마이그레이션이 여기서 실행되므로 가장 먼저 띄운다
cd backend
python3 -m venv .venv && source .venv/bin/activate   # scripts/check.sh가 backend/.venv를 찾는다
pip install -e ".[dev]"          # 기본은 가짜 임베딩이다. 실제 BGE-M3는 아래 「임베딩 프로바이더」
uvicorn app.main:app --reload

# 3. 최초 관리자 계정 — 자체 가입이 없으므로 첫 로그인 전에 한 번 실행한다
cd ..
ADMIN_PASSWORD='<초기 비밀번호>' python scripts/create_admin.py admin --admin

# 4. 임베딩 워커 (별도 터미널) — API 서버를 먼저 기동해야 한다.
#    마이그레이션은 API startup에서만 실행되므로(ADR-012), 순서를 바꾸면
#    워커가 "스키마 없음"으로 실패한다.
cd backend && source .venv/bin/activate
python -m app.worker

# 5. 프론트엔드 (별도 터미널)
cd frontend
npm install && npm run dev

# 6. MCP 서버 (Claude Desktop/Code가 기동한다. 수동 확인은 아래 커맨드)
cd backend && source .venv/bin/activate
MCP_USER_ID=alice python -m mcp_server.server
```

`http://localhost:3000` 접속.

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
모델은 첫 임베딩 때 내려받아 캐시하므로 최초 1회는 시간이 걸립니다.

### 인증 환경변수

| 환경변수 | 기본값 | 설명 |
|---|---:|---|
| `SESSION_LIFETIME_HOURS` | `24` | 서버 세션과 로그인 쿠키의 수명(시간) |
| `SESSION_COOKIE_SECURE` | `false` | 로컬 HTTP에서는 `false`. HTTPS 상시 배포에서는 반드시 `true` |

초기 계정은 환경변수로 자동 생성되지 않는다. API 서버를 먼저 기동해 마이그레이션을 적용한 뒤 위의
`scripts/create_admin.py`를 실행한다. 비밀번호를 셸 기록에 남기고 싶지 않으면 `ADMIN_PASSWORD`를
생략하면 대화형으로 입력받는다. 이후 관리자는 `/admin/users`에서 일반 사용자나 다른 관리자를
발급할 수 있다. 관리자 권한은 계정 관리 전용이며 다른 사용자의 private 문서를 열람하게 하지는 않는다.

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
        "MCP_USER_ID": "alice"
      }
    }
  }
}
```

등록되는 도구는 `search_documents`, `get_document`, `list_documents`, `create_document` 네 개다.
`DATABASE_URL`, `EMBEDDING_PROVIDER`, `MCP_USER_ID`를 MCP 프로세스에 함께 전달해야 한다.
`MCP_USER_ID`를 생략하면 public 문서 읽기만 가능하고 `create_document` 쓰기는 거부된다. MCP 서버는
마이그레이션을 실행하지 않으므로 API 서버를 먼저 기동해 스키마가 적용된 상태여야 한다 (ADR-012·036).

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
세션에서 `POST /api/auth/tokens`로 API 토큰을 한 번 발급한 뒤 Bearer 자격증명만 사용할 수 있습니다.
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

애플리케이션은 OpenProxy VIP 단일 엔드포인트만 바라봅니다. 환경변수만 바꾸면 됩니다.

```bash
DATABASE_URL="postgresql://app@<vip>:6432/<pool_name>"
```

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
