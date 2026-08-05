# OpenArchive

**문서를 고치면 DB가 벡터를 따라 맞추는 AI 문서 저장소**

> AI에게 주는 근거가 어느 시점에도 **하나의 일관된 버전**이고, **최신 버전으로 수렴**함을 데이터베이스가 보장합니다.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PostgreSQL](https://img.shields.io/badge/OpenSQL-PostgreSQL%2017-336791.svg)](https://docs.tibero.com/tmaxopensql/overview)
[![Embedding](https://img.shields.io/badge/embedding-BGE--M3%20(MIT)-orange.svg)](https://huggingface.co/BAAI/bge-m3)

> 2026년 오픈소스 개발자대회 기업 지정과제 출품작
> **현재 상태**: DB 계층(트리거·잡 큐·워커)과 백엔드 API·하이브리드 검색까지 동작합니다. 프론트엔드 화면과 MCP 서버는 진행 중입니다.

---

## 무엇을 해결하는가

문서 검색에 벡터 DB를 붙이면 흔히 이런 문제가 생깁니다.

1. **원본과 벡터가 어긋난다.** 문서를 고쳤는데 임베딩 갱신을 깜빡하거나, 별도 파이프라인이 실패하면 검색 결과가 옛날 내용을 가리킵니다.
2. **DB가 죽으면 서비스가 죽는다.** 단일 DB 장애가 곧 전체 장애입니다.

OpenArchive는 이 둘을 **DB 계층에서** 해결합니다.

- **정합성**: 문서를 수정하면 트리거가 **같은 트랜잭션 안에서** 재임베딩 작업을 만듭니다. 애플리케이션 코드에는 임베딩 호출이 없습니다. "문서만 저장되고 벡터 갱신은 유실되는" 상태가 구조적으로 불가능합니다.
- **고가용성**: [OpenSQL](https://docs.tibero.com/tmaxopensql/overview) 클러스터 위에서 동작합니다. Primary 노드에 장애가 나도 OpenProxy가 새 Primary로 재연결하고, 미처리 임베딩 작업은 복제된 큐에 남아 그대로 재개됩니다.

### 무엇을 보장하고, 무엇을 보장하지 않는가

"항상 최신"이라고 말하지 않습니다. 재임베딩 중에는 이전 버전 벡터로 검색되고, 워커 폴링 주기(5초)와 임베딩 소요만큼 반영이 늦으며, 장애 복구 구간에는 요청이 실패합니다.

| 보장한다 | 보장하지 않는다 |
|---|---|
| **버전 일관성** — 검색되는 청크는 어느 시점에도 하나의 버전이며 섞이지 않습니다 | 즉시 반영 |
| **최신으로 수렴** — 지연은 있어도 결국 최신 버전에 도달합니다 | 무중단 |
| **관측 가능성** — 어긋난 구간을 쿼리 한 줄로 셀 수 있습니다 | |

```sql
-- 원본과 벡터가 어긋난 문서. 파이프라인이 정상이면 0으로 수렴한다.
SELECT count(*) FROM documents d
  JOIN document_chunks c ON c.document_id = d.id
 WHERE c.version <> d.version;
```

문서를 고치면 이 값이 잠깐 올랐다가 워커 처리 후 0으로 돌아옵니다. 장애를 일으켜도 결국 0으로 수렴합니다. **증명할 수 있는 주장을 하는 것**이 "항상 최신"이라고 쓰는 것보다 낫다고 판단했습니다 ([ADR-015](docs/ADR.md)).

---

## 동작 방식

```
문서 업로드
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
```

**핵심은 "애플리케이션이 임베딩 파이프라인을 조율하지 않는다"는 점입니다.** 업로드 API는 `INSERT`만 합니다. 나머지는 DB가 합니다.

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| 자동 임베딩 파이프라인 | PDF·DOCX·TXT·MD 업로드 시 트리거가 작업 생성, 워커가 청킹·임베딩·저장 |
| 하이브리드 검색 | 정형 필터(태그·유형·권한)와 벡터 유사도를 **하나의 SQL**로 결합 |
| 텍스트 버전 관리·자동 재임베딩 | 플랫폼 안에서 **추출 텍스트를 직접 편집**. 수정하면 이력이 쌓이고 재임베딩이 자동 기동. 처리 중에는 이전 벡터로 검색이 계속됨 |
| 관련 문서·태그 추천 | 청크 평균 벡터로 유사 문서를 찾고, 그 문서들의 태그를 추천. 검색과 동일한 권한 필터 적용 |
| 장애 자동 복구 | Primary 장애 시 재연결, 미처리 작업 무손실 재개 |
| MCP 근거 게이트웨이 | AI 에이전트에 사내 문서의 근거를 공급. 답변 생성은 클라이언트가 수행 |

> **원본 파일은 보관하지 않습니다.** 업로드된 PDF·DOCX에서 텍스트만 추출해 저장하며, 편집·버전 관리·임베딩의 대상은 그 **추출 텍스트**입니다. 원본 파일 다운로드는 지원하지 않습니다 ([ADR-017](docs/ADR.md)).

---

## 기술 스택

| 영역 | 사용 기술 |
|---|---|
| DB | [Tmax OpenSQL v3](https://docs.tibero.com/tmaxopensql/overview) (PostgreSQL 17 + pgvector) · OpenHA(Patroni) · OpenHA DCS(etcd) · OpenProxy |
| 백엔드 | Python 3.12+ · FastAPI · psycopg3 |
| 임베딩 | [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) (MIT, 1024차원) — 로컬 구동 |
| 프론트엔드 | Next.js (App Router) · TypeScript · Tailwind CSS |
| MCP | Python `mcp` SDK (FastMCP, stdio) |

---

## 빠른 시작

> ⚠️ 아래 절차는 완성 시점 기준입니다. 현재 **4번(프론트엔드)은 화면이 없습니다** — 업로드·검색·상태 조회는 API로 확인하십시오.

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
pip install -e ".[dev]"          # 실제 임베딩(BGE-M3)까지 쓸 때는 ".[dev,local]"
uvicorn app.main:app --reload

# 3. 임베딩 워커 (별도 터미널) — API 서버를 먼저 기동해야 한다.
#    마이그레이션은 API startup에서만 실행되므로(ADR-012), 순서를 바꾸면
#    워커가 "스키마 없음"으로 실패한다.
cd backend && source .venv/bin/activate
python -m app.worker

# 4. 프론트엔드 (별도 터미널)
cd frontend
npm install && npm run dev
```

`http://localhost:3000` 접속.

### API 확인

엔드포인트 목록과 요청·응답 스키마는 서버가 직접 제공합니다. 구현과 어긋날 수 없는 유일한 출처입니다.

```
http://localhost:8000/docs
```

설계 의도와 각 엔드포인트의 근거는 [Architecture](docs/ARCHITECTURE.md#api-설계)에 있습니다.

### 검증

```bash
bash scripts/check.sh    # 백엔드 lint+test, 프론트엔드 lint+test+build
```

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
