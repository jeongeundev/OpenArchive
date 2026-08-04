# 프로젝트: OpenSQL AI 문서관리 플랫폼

오픈소스 개발자 대회 지정과제. **Tmax OpenSQL** 위에서 **문서 업로드 → 자동 임베딩 → 벡터 동기화 → 하이브리드 검색**이 하나의 파이프라인으로 동작하는 AI 문서관리 플랫폼을 만든다.

## 문서 (작업 전 읽을 것)

| 파일 | 내용 | 언제 읽나 |
|---|---|---|
| `docs/PROJECT_CONTEXT.md` | 기업 요구사항, 1차 평가 기준, 설계 원칙 | 범위·우선순위를 판단할 때 |
| `docs/OPENSQL_RESEARCH.md` | **OpenSQL 공식 문서 조사 결과** + 미확인 항목 + M0 검증 목록 | **인프라·접속·인덱스 관련 결정 전 반드시** |
| `docs/ADR.md` | 설계 결정의 근거와 트레이드오프 | 설계 결정을 바꾸거나 추가할 때 |
| `docs/ARCHITECTURE.md` | 스키마·트리거·워커·검색·HA 상세 | 구현할 때 |
| `docs/PRD.md`, `docs/UI_GUIDE.md` | 기능 범위, UI 규칙 | 화면·기능 작업 시 |

> ⚠️ **OpenSQL은 단일 DBMS가 아니라 4컴포넌트 제품이다** — PostgreSQL 16(또는 14) + OpenHA Cluster Manager(Patroni) + OpenHA DCS(etcd) + **OpenProxy**(Rust 커넥션 풀러, VRRP VIP). 일반 PostgreSQL 관례로 추론하지 말고 `docs/OPENSQL_RESEARCH.md`를 먼저 확인할 것. 이 문서를 읽지 않아 ADR-006을 한 번 틀리게 썼다.

## 기술 스택
- 백엔드: Python 3.12+, FastAPI, psycopg3 (+psycopg_pool), pytest
- 프론트엔드: Next.js (App Router), TypeScript strict mode, Tailwind CSS
- DB: Tmax OpenSQL v3 (PostgreSQL 16 + pgvector·pgvectorscale 번들). 애플리케이션은 **OpenProxy VIP:6432** 경유. 로컬 개발은 `pgvector/pgvector:pg16` 단일 컨테이너 (OpenSQL 공식 Docker 배포판 없음)
- 임베딩: sentence-transformers **`BAAI/bge-m3`** (MIT, 1024차원) 단일. 테스트용 `FakeProvider`만 예외. **상용 API 모델 금지** — 대회 규정 (ADR-003)
- MCP 서버: Python `mcp` SDK (FastMCP, stdio transport)

## 아키텍처 규칙
- CRITICAL: 임베딩 파이프라인의 트리거링(잡 생성·코얼레싱·NOTIFY)은 반드시 DB 계층(트리거·제약·인덱스)에서 처리한다. 애플리케이션 코드에서 `embedding_jobs`에 직접 INSERT 하지 마라. 이유: "원본-벡터 정합성이 DB 안에서 보장된다"가 이 과제의 심사 핵심이다.
- CRITICAL: 검색은 정형 필터(태그·유형·권한) + 벡터 유사도를 **단일 SQL 쿼리**로 결합한다. DB에서 넓게 가져와 애플리케이션에서 후처리 필터링하지 마라. 이유: 정형+벡터 하이브리드 활용이 가산점 항목이다.
- CRITICAL: DB 접속은 **OpenProxy VIP 단일 엔드포인트**로 한다. 애플리케이션에 멀티호스트 DSN이나 `target_session_attrs`를 두지 마라. DSN은 환경변수(`DATABASE_URL`)로만 주입한다. 이유: 새 프라이머리 발견·재연결은 OpenProxy가 수행한다. 앱에서 중복 구현하면 OpenSQL 공식 아키텍처를 우회하게 된다 (ADR-006).
- CRITICAL: 검색 쿼리는 **plain `BEGIN` … `COMMIT`** 블록 안에서 실행한다. `BEGIN READ ONLY`를 쓰지 마라 — OpenProxy가 이를 Replica로 라우팅해 방금 임베딩된 청크가 누락된다. 이유: 트랜잭션 밖 단순 SELECT는 Replica로 가고, 복제 지연 보장이 없다 (ADR-010).
- CRITICAL: 워커는 **폴링을 주 경로**로 잡을 드레인한다. `LISTEN`/`NOTIFY`는 최적화이며, 없어도 파이프라인이 동작해야 한다. 이유: OpenProxy 경유 시 LISTEN 동작이 문서로 보장되지 않는다 (ADR-009).
- CRITICAL: **관련 문서·태그 추천 쿼리에도 검색과 동일한 `visibility`/`owner_id` 필터를 적용한다.** 빠뜨리면 private 문서가 관련 문서로 노출되고 그 태그가 추천으로 새어나간다 (ADR-018).
- CRITICAL: **`avg(embedding)`을 쓰는 쿼리는 호출 전에 청크 존재를 확인한다.** 청크가 0행이면 `avg`가 NULL을 반환하고 `embedding <=> NULL`이 정렬을 무의미하게 만들어, **에러 없이 무작위 문서 목록이 반환된다.** 분기 기준은 `embedding_status`가 아니라 **청크 존재 여부**다 — 재임베딩 중에는 이전 청크로 정상 응답해야 한다.
- CRITICAL: 문서당 1건으로 줄이는 벡터 쿼리는 **벡터 정렬 + LIMIT → `DISTINCT ON` → 거리순 재정렬 + 최종 LIMIT** 순서를 지킨다. `DISTINCT ON` 직후에 `LIMIT`을 붙이면 유사도가 아니라 `document_id`(UUID) 순으로 잘린다 (ADR-011).
- 사용자 대상 문구에 **"항상 최신"·"실시간 동기화"를 쓰지 않는다.** 보장 범위는 **버전 일관성 + 최신 수렴**이다 (ADR-015). "무중단"을 "짧은 중단 후 자동 복구"로 쓰는 것과 같은 원칙이다.
- 편집·버전 관리의 대상은 **추출 텍스트**이며 원본 파일이 아니다. 원본 파일은 보관하지 않는다. 문서·UI에서 **원본 파일 / 추출 텍스트 / 텍스트 버전**을 구분해 쓰고 "원문"으로 뭉뚱그리지 않는다 (ADR-017).
- 벡터 컬럼은 `vector(1024)` 고정. 임베딩 프로바이더가 바뀌어도 차원은 바꾸지 않는다.
- 스키마 변경은 `backend/migrations/`의 번호 붙은 raw SQL 파일로만 한다 (ORM 마이그레이션 도구 금지).
- 백엔드 비즈니스 로직은 `backend/app/services/`에 두고, API 라우터와 MCP 서버는 이를 재사용만 한다.

## 개발 프로세스
- CRITICAL: 새 기능 구현 시 반드시 테스트를 먼저 작성하고, 테스트가 통과하는 구현을 작성할 것 (TDD). Python도 동일 적용 (`backend/tests/test_*.py`).
- CRITICAL: 테스트를 통과시키려고 검증 로직을 약화하거나, 실패하는 테스트를 skip·주석 처리·삭제하지 마라. `assert True` 같은 무의미한 assertion, 예외만 잡고 아무것도 검증하지 않는 패턴도 금지. 이유: tdd-guard 훅은 테스트 파일의 "존재"만 확인하므로 빈 껍데기 테스트로 우회된다.
- CRITICAL: 마이그레이션 SQL도 TDD 대상이다. `*_triggers.sql`·`*_tables.sql`은 tdd-guard 훅이 대응 테스트(`backend/tests/test_triggers.py`, `test_tables.py`)를 요구한다. `*_extensions.sql`·`*_indexes.sql`은 훅에서 제외되지만, 인덱스가 검색 계획에 실제로 쓰이는지 확인이 필요하면 테스트를 직접 추가하라.
- CRITICAL: DB 의존 테스트를 Mock·SQLite·인메모리 가짜 구현으로 대체하지 마라. 실제 `pgvector/pgvector:pg16` 컨테이너에 마이그레이션을 적용한 상태로 검증한다. 이유: 트리거·NOTIFY·`vector` 연산자 동작은 원리상 Mock으로 검증할 수 없고, 그것이 이 과제의 심사 핵심이다.
- 검증 명령(`bash scripts/check.sh`)을 실행하지 못했다면 추측으로 통과 처리하지 말고, 실행하지 못한 이유와 영향 범위를 응답에 명시하라.
- 커밋은 Conventional Commits `<type>(<scope>): <설명>` 형식. 타입: `feat` `fix` `docs` `test` `refactor` `chore` `ci` `perf` / 스코프: `db` `worker` `api` `search` `mcp` `frontend` `adr` `harness`(`scripts/execute.py`·`.claude/commands/harness.md`)
- 브랜치는 `feat/` `fix/` `docs/` `test/` `chore/` 5개 접두사만 사용. `main` 단일 기본 브랜치에 Squash merge (ADR-013)
- **TDD 강제는 커밋 순서가 아니라 `scripts/hooks/tdd-guard.sh`가 한다.** 이 훅은 `PreToolUse(Edit|Write)`로 걸려, 대응 테스트가 없는 구현 파일 쓰기를 `deny`로 차단한다. 커밋 순서는 사후 기록이지만 훅은 사전 차단이므로 더 강한 보장이다
- 커밋 단위는 작업 방식에 따라 다르다
  - **수동 작업**: 실패하는 `test:` 커밋을 먼저, 통과시키는 `feat:` 커밋을 나중에
  - **하네스 실행**(`scripts/execute.py`): step당 커밋 1개. 테스트와 구현이 한 커밋에 들어간다. step을 test/feat으로 쪼개지 마라 — 훅이 이미 순서를 강제하고, squash merge하면 `main`에서 그 순서가 사라진다

## 명령어
```bash
docker compose up -d                 # 로컬 DB (pgvector 컨테이너)

# 백엔드 (backend/ 에서)
python3 -m venv .venv                # 가상환경 생성 (최초 1회)
source .venv/bin/activate            # 활성화 — 아래 명령은 활성화 상태 전제
pip install -e ".[dev]"              # 의존성 설치
uvicorn app.main:app --reload        # API 개발 서버
python -m app.worker                 # 임베딩 워커 (별도 프로세스)
ruff check .                         # 린트
pytest                               # 테스트

# 프론트엔드 (frontend/ 에서)
npm run dev / build / lint / test

bash scripts/check.sh                # 통합 검증 (backend + frontend)
```
