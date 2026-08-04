# Step 0: 로컬 개발용 pgvector 컨테이너

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ADR.md` — **ADR-007**(일상 개발은 pgvector 단일 컨테이너, OpenSQL은 별도 VM), **ADR-012**(마이그레이션은 API 서버만 실행)
- `/docs/ARCHITECTURE.md` — "디렉토리 구조" 절
- `/README.md` — "빠른 시작" 절. 여기에 적힌 실행 절차와 어긋나는 파일을 만들지 마라
- `/scripts/check.sh` — 통합 검증이 무엇을 실행하는지
- `/.gitignore` — `.env`는 추적 대상이 아니다

이 step은 phase의 첫 step이므로 이전 step 산출물은 없다.

## 배경

이 저장소에는 아직 코드가 없다. 이 phase는 "누구나 clone 후 실행 가능한" 뼈대를 만드는 것이 목적이며, 이 step은 그중 **로컬 DB 환경**만 담당한다.

실제 제품이 붙는 DB는 Tmax OpenSQL(PostgreSQL 17.8 + pgvector 0.8.1)이지만, OpenSQL은 x86-64 Rocky Linux 전용이라 Apple Silicon에서는 에뮬레이션이 필요하다. 그래서 일상 개발은 `pgvector/pgvector:pg17` 컨테이너로 하고, OpenSQL 고유 동작만 별도 VM에서 확인한다 (ADR-007).

## 작업

### 1. `docker-compose.yml` (저장소 루트)

서비스 **하나만** 정의한다.

| 항목 | 값 |
|---|---|
| 서비스명 | `db` |
| 이미지 | `pgvector/pgvector:pg17` — 태그 고정 |
| 포트 | `5432:5432` |
| 사용자 / DB / 비밀번호 | `openarchive` / `openarchive` / `openarchive` (로컬 전용) |
| 볼륨 | named volume `pgdata` → `/var/lib/postgresql/data` |
| healthcheck | `pg_isready -U openarchive -d openarchive` |

규칙:

- 환경변수는 `${POSTGRES_USER:-openarchive}` 형태로 **기본값을 compose 파일 안에 둔다.** 이유: `.env`가 `.gitignore` 대상이므로, clone 직후 `.env` 없이도 `docker compose up -d`가 성공해야 한다. 그것이 이 phase의 목적이다.
- 최상위 `version:` 키는 넣지 않는다. Compose v2에서 obsolete 경고가 난다.
- healthcheck의 `interval`/`timeout`/`retries`는 컨테이너가 수 초 안에 `healthy`로 보고되도록 잡는다.

### 2. `.env.example` (저장소 루트)

애플리케이션이 읽는 환경변수의 예시. 최소 두 개:

```
DATABASE_URL=postgresql://openarchive:openarchive@localhost:5432/openarchive
EMBEDDING_PROVIDER=fake
```

- 실 OpenSQL 클러스터로 전환할 때 무엇을 바꾸는지 **주석 한 줄**로 적는다: OpenProxy VIP의 `6432` 포트 단일 엔드포인트로 `DATABASE_URL`만 교체한다 (ADR-006).
- 멀티호스트 DSN이나 `target_session_attrs`를 예시에 쓰지 마라 (아래 금지사항).

### 3. `README.md` 정합성 확인

`README.md`의 "빠른 시작"에 이미 `docker compose up -d`가 적혀 있다. 컨테이너 이름·포트·자격증명이 README와 어긋나면 README를 고치지 말고 **compose 쪽을 README에 맞춰라.** README를 고쳐야만 하는 경우에만 최소 범위로 수정하고, 무엇을 왜 고쳤는지 summary에 적어라.

## Acceptance Criteria

```bash
# 문법 검증 (Docker daemon 없이도 통과해야 한다)
docker compose config -q

# 기동 및 상태
docker compose up -d
docker compose ps

# 접속 가능성
docker compose exec -T db pg_isready -U openarchive -d openarchive

# PostgreSQL 메이저 버전이 17인지
docker compose exec -T db psql -U openarchive -d openarchive -tAc "SHOW server_version;"

# pgvector 확장이 이미지에 포함되어 있는지 (설치는 하지 않는다)
docker compose exec -T db psql -U openarchive -d openarchive -tAc \
  "SELECT default_version FROM pg_available_extensions WHERE name='vector';"

# 통합 검증 (이 step 시점에는 backend/frontend가 없어 둘 다 '건너뜀'이 정상)
bash scripts/check.sh
```

기대값: `server_version`이 `17.`로 시작하고, `pg_available_extensions` 조회가 빈 값이 아니어야 한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ADR-007의 이미지 태그(`pg17`)를 지켰는가?
   - ADR-012를 위반하는 자동 스키마 적용이 compose에 들어가지 않았는가?
   - `README.md`의 빠른 시작 절차와 일치하는가?
3. 결과에 따라 `phases/m0-scaffold/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - **Docker daemon이 기동되어 있지 않으면** → `"status": "blocked"`, `"blocked_reason": "Docker daemon 미기동 — Docker Desktop 실행 필요"` 후 즉시 중단. 이 경우 파일은 저장하되 AC의 `docker compose config -q`까지만 검증한 상태를 기록하라.

## 금지사항

- **compose에 스키마 적용을 넣지 마라** (`/docker-entrypoint-initdb.d` 마운트, `command` 오버라이드, init SQL 등). 이유: 마이그레이션 실행 주체는 API 서버 하나로 고정되어 있다 (ADR-012). 두 주체가 같은 스키마를 적용하면 경쟁이 생긴다.
- **`CREATE EXTENSION vector`를 이 step에서 실행하지 마라.** 이유: 확장 설치는 `backend/migrations/001_extensions.sql`의 몫이며 후속 phase 범위다. 여기서는 이미지에 확장이 **포함되어 있는지**만 확인한다.
- **backend·frontend 서비스를 compose에 추가하지 마라.** 이유: API·워커·프론트는 맥 네이티브로 실행한다 (ADR-007). 컨테이너로 감싸면 임베딩 모델 추론이 느려지고 개발 루프가 망가진다.
- **이미지 태그를 `latest`나 `pg16`으로 바꾸지 마라.** 이유: 실 배포판이 PostgreSQL 17.8이라 메이저를 맞춘 것이다 (ADR-007). 과거 문서에 `pg16`이 남아 있어도 그것은 개정 전 값이다.
- **`.env` 파일을 만들지 마라.** `.env.example`만 만든다. 이유: `.gitignore`가 `.env`를 제외하고 있으며, 로컬 비밀값을 저장소에 넣지 않기 위함이다.
- **`.env.example`에 멀티호스트 DSN이나 `target_session_attrs`를 쓰지 마라.** 이유: 새 프라이머리 발견·재연결은 OpenProxy가 수행한다. 앱에서 중복 구현하면 OpenSQL 공식 아키텍처를 우회한다 (ADR-006).
- backend/ 나 frontend/ 디렉토리를 만들지 마라. 이유: 각각 step 1, step 2의 범위다.
- 기존 테스트를 깨뜨리지 마라.
