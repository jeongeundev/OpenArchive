# 운영 가이드

README 「시작하기」로 기동한 뒤, 설정을 바꾸거나 운영하면서 걸리기 쉬운 것들을 모았습니다.
기동 절차 자체는 [README](../README.md#시작하기)가 정본이며 여기에 복제하지 않습니다.

## 환경변수 파일

애플리케이션 설정 파일은 **`backend/.env` 하나**입니다. 기본값만으로 README 절차가 완주하므로
설정을 바꿀 때만 만듭니다.

```bash
cd backend && cp .env.example .env
```

설정을 읽는 주체는 API·워커·MCP 서버와 `scripts/create_admin.py` 넷인데 실행 디렉토리가 서로
다릅니다. `app/config.py`가 `backend/.env` 한 곳만 절대경로로 읽어 넷이 같은 값을 보게 합니다.
환경변수를 직접 주는 방식(`DATABASE_URL=... uvicorn ...`)은 언제나 파일보다 우선합니다.

> **저장소 루트의 `.env`는 다른 파일입니다.** 애플리케이션은 읽지 않지만 **`docker compose`가
> 읽습니다** — `docker-compose.yml`의 `${POSTGRES_USER:-openarchive}` 세 자리를 채우는 것이 그
> 파일입니다. 로컬 DB의 자격증명·DB 이름을 바꾸려면 루트 `.env`에 `POSTGRES_*`를 두고, 앱이 붙을
> 주소는 `backend/.env`의 `DATABASE_URL`에 둡니다. 한쪽에 몰아 쓰면 컨테이너와 앱이 서로 다른
> DB를 가리킵니다.

| 환경변수 | 기본값 | 설명 |
|---|---:|---|
| `DATABASE_URL` | 로컬 컨테이너 | 실 OpenSQL은 OpenProxy 단일 엔드포인트 `postgresql://app@<vip>:6432/<pool_name>` (ADR-006) |
| `EMBEDDING_PROVIDER` | `fake` | `local` — `BAAI/bge-m3` · `fake` — 테스트용. 아래 「임베딩 프로바이더」 |
| `ZOMBIE_TIMEOUT_MINUTES` | `5` | 좀비 잡 회수 임계. `0`은 단일 워커 복구 데모에서만 |
| `SESSION_LIFETIME_HOURS` | `24` | 서버 세션과 로그인 쿠키의 수명 |
| `SESSION_COOKIE_SECURE` | `false` | 로컬 HTTP에서는 `false`. HTTPS 상시 배포에서는 반드시 `true` |

## 임베딩 프로바이더

**기본값 `fake`는 의미 없는 벡터를 만듭니다.** 이 상태에서도 업로드·검색이 동작하기 때문에
알아채기 어렵습니다. 검색 품질을 판단하거나 시연하려면 `local`로 바꿔야 합니다.

```bash
pip install -e ".[dev,local]"                              # sentence-transformers + torch (수 GB)
EMBEDDING_PROVIDER=local uvicorn app.main:app --reload     # API — 검색 질의를 임베딩한다
EMBEDDING_PROVIDER=local python -m app.worker              # 워커 — 문서를 임베딩한다
```

**API·워커·MCP 서버는 각자 프로바이더를 생성하므로 세 프로세스에 같은 값을 주어야 합니다.**
값이 엇갈리면 질의 벡터와 문서 벡터가 다른 공간에 놓여, 에러 없이 검색 결과만 무의미해집니다.
모델은 API·워커가 **기동할 때** 내려받아 캐시하므로 최초 1회는 기동이 오래 걸리고, 대신 첫
검색·첫 업로드가 로딩을 기다리지 않습니다 (ADR-003 보강).

## 프로세스 구성

### `openarchive init`

도입할 DB를 준비 상태로 만듭니다 — **연결 확인 → capability 확인(PostgreSQL 버전·`vector`·
`pg_trgm`·CREATE 권한) → 마이그레이션 적용 → 준비 상태 보고**. 확인이 적용보다 먼저이므로,
확장이 없거나 권한이 모자라면 스키마를 건드리기 전에 무엇이 왜 필요한지 알려주고 멈춥니다.

```bash
openarchive init                                                         # 대화형 — DSN 한 줄만 입력
openarchive init --dsn "postgresql://app@<vip>:6432/<pool_name>" --yes   # 비대화형
```

DSN을 확인한 뒤 `backend/.env`의 `DATABASE_URL` 줄만 갈아 끼웁니다. 다른 설정은 보존됩니다.

> **기존 데이터베이스를 덮어쓰지 않습니다.** `schema_migrations`가 없는데 OpenArchive가 쓰는
> 테이블 이름(`documents`·`users` 등)이 이미 있으면 아무것도 바꾸지 않고 중단합니다.
> 마이그레이션에 `ALTER TABLE documents`가 있어, 같은 이름의 다른 테이블 위에서 돌면 그 데이터가
> 손상되기 때문입니다 (ADR-039).

**하지 않는 것**: API·워커·프론트 기동, DB 자동 탐색·설치, 문서 공급, 계정 생성. 이 명령을
건너뛰어도 API 서버가 startup에서 같은 마이그레이션을 적용하므로(ADR-012), init은 **필수가 아니라
사전 점검**입니다.

### `openarchive serve`

API 서버와 임베딩 워커를 함께 띄웁니다. **워커를 따로 켜는 것을 잊을 수 없게 하는 것**이
목적입니다 — 워커가 없으면 업로드는 성공하는데 검색에 잡히지 않고, 에러가 나지 않아 원인을
짐작하기 어렵습니다.

```bash
openarchive serve                              # 127.0.0.1:8000
openarchive serve --host 0.0.0.0 --port 9000
```

- 웹 화면·API·워커가 한 주소에서 나옵니다. 별도 프론트 서버가 필요 없습니다.
- Ctrl-C 한 번에 둘 다 멈춥니다. 워커는 **처리 중인 잡을 마치고** 종료합니다.
- `kill <pid>`·컨테이너 진입점·감독자의 stop처럼 **SIGTERM이 부모에게만 와도** 자식까지
  함께 내려갑니다. 정리 도중 신호가 한 번 더 와도 정리를 끝까지 마칩니다.
- 한쪽이 멈추면 나머지도 내리고 0이 아닌 코드로 끝납니다 — 반쪽만 도는 상태를 만들지 않습니다.
- **죽은 프로세스를 되살리지는 않습니다.** 배포 호스트에서는 systemd가 그 역할을 합니다
  (ADR-038 · `scripts/openarchive-worker.service`).

### 기동 순서

스키마를 준비하는 것은 `openarchive init`과 API 서버뿐입니다 (ADR-012·039). 워커나 MCP 서버를
먼저 띄우면 스키마가 없어 실패합니다.

### 워커 장애

워커 프로세스가 강제 종료되면 systemd 유닛이 되살리고, 재기동한 워커가 방치된 잡을
`ZOMBIE_TIMEOUT_MINUTES` 뒤에 회수합니다. 그 회수를 기다리는 동안에도 나머지 잡은 계속 처리되며,
워커를 반복적으로 죽이는 잡은 재시도 예산을 소진한 뒤 `error`로 격리되어 파이프라인을 막지
않습니다 (ADR-038).

## 인증과 계정

초기 계정은 환경변수로 자동 생성되지 않습니다. 스키마가 적용된 뒤 `scripts/create_admin.py`를
실행합니다. 비밀번호를 셸 기록에 남기고 싶지 않으면 `ADMIN_PASSWORD`를 생략하면 대화형으로
입력받습니다.

```bash
ADMIN_PASSWORD='<초기 비밀번호>' python scripts/create_admin.py admin --admin
```

이후 관리자는 `/admin/users`에서 일반 사용자나 다른 관리자를 발급합니다. **관리자 권한은 계정 관리
전용**이며 다른 사용자의 private 문서를 열람하게 하지는 않습니다.

각 사용자는 `/settings`에서 자기 비밀번호를 바꾸고 API 토큰을 발급·폐기합니다. 비밀번호를 바꾸면
그 계정으로 열려 있던 모든 기기의 로그인이 끊기고, 발급한 API 토큰은 영향을 받지 않습니다 (ADR-040).

**비밀번호를 잊어 로그인조차 못 하는 계정**은 운영자가 서버에서 재설정합니다. 관리 화면에는 남의
비밀번호를 바꾸는 경로를 두지 않습니다 — 관리자가 남의 계정을 탈취해 그 사람의 private 문서를
읽게 되면 "관리자 권한은 계정 관리 전용"이라는 경계가 무너지기 때문입니다.

```bash
cd backend && source .venv/bin/activate
openarchive reset-password alice     # 새 비밀번호는 화면에 남지 않게 입력받는다
```

재설정하면 그 계정의 로그인 세션이 모두 끊깁니다. 발급된 API 토큰은 그대로 유효하므로, 자격증명까지
갈아야 하면 다시 로그인해 `/settings`에서 폐기합니다.

### API 토큰

기본 scope는 `read`이며 문서 공급에는 `read_write`가 필요합니다. 원문 토큰은 발급 응답에만 나오고
`GET /api/auth/tokens` 목록에는 다시 나타나지 않습니다. 발급·목록·폐기와 `/api/admin/*`는 세션
전용입니다 — 토큰이 토큰을 발급하면 폐기 뒤에도 자격증명을 스스로 재생할 수 있기 때문입니다
(ADR-034). `POST /api/auth/tokens`를 직접 호출해도 됩니다.

`examples/ingest_text.py`의 실제 서버 완주는 CI가 확인하지 않으므로 API·워커·DB를 함께 기동한
환경에서 실행합니다.

### MCP 서버

MCP 서버는 HTTP를 거치지 않고 서비스를 직접 호출하므로 API 인증 경계와 무관하며, 열람 범위는
`MCP_USER_ID`가 정합니다. `DATABASE_URL`·`EMBEDDING_PROVIDER`·`MCP_USER_ID`를 MCP 프로세스에
함께 전달해야 하고, MCP 서버는 마이그레이션을 실행하지 않으므로 스키마가 적용된 상태여야
합니다 (ADR-012·036).

> ⚠️ **`MCP_USER_ID`는 실존 계정인지 검증되지 않습니다.** 미설정·빈 값·공백은 거부되지만, 그
> 검사를 통과한 이름은 `users` 테이블에 없어도 그대로 문서 소유자가 됩니다 (ADR-036). 실제
> 계정명과 정확히 같게 적어야 `create_document`로 만든 문서가 Web UI에서 자기 문서로 보입니다.
> 생략하면 public 문서 읽기만 가능하고 `create_document`는 거부됩니다.

## 실 OpenSQL에서만 검증되는 것

로컬 `pgvector/pgvector:pg17` 컨테이너로 완주되는 범위와 실 OpenSQL 환경이 필요한 범위는
다음과 같습니다 (ADR-007).

| 라이선스 없이 검증할 수 있는 범위 | 실 OpenSQL 환경이 필요한 검증 |
|---|---|
| 자동 임베딩 파이프라인 전 구간 — 트리거·아웃박스·`SKIP LOCKED`·청킹·임베딩 | OpenProxy 경유 세션 동작 (ADR-009) |
| 하이브리드 검색 · 관계 그래프 · 태그 추천 · 군집 | 읽기/쓰기 분리와 복제 지연 (ADR-010) |
| 텍스트 버전·되돌리기, 권한 모델, API 토큰 | Patroni 리더 선출·승격 |
| Web UI · REST API · MCP 서버 전체 | 장애 복구 데모 (`scripts/demo_recovery.sh`) |
| `bash scripts/check.sh` 전체 통과 | 라이선스·번들 확장 실동작 |

### OpenProxy 풀이 바라보는 데이터베이스

설치기는 `opensql` 데이터베이스를 만들어 놓고 정작 풀은 관리용 `postgres`를 바라보게 설정합니다.
클라이언트는 DSN에 **풀 이름**을 적으므로 실제 저장 위치가 드러나지 않아, 그대로 두면
마이그레이션과 문서가 `postgres`에 쌓입니다. 교정 절차는 [OpenSQL 환경 구축](SETUP_OPENSQL.md)의
§10 「풀이 바라보는 데이터베이스를 교정한다」에 있습니다.

## 복구 데모

DB 프로세스 장애에서의 자동 복구를 단일 타임라인으로 확인합니다. 마이그레이션이 적용된
**실 OpenSQL VM**과 `backend/.venv`의 개발 의존성이 필요하며, 로컬 Docker DB에서는 실행할 수
없습니다. 스크립트가 API와 워커를 직접 띄우므로 별도로 실행해 둘 필요는 없습니다.

```bash
# 기본값: OPENSQL_HOST=192.168.64.4, OPENSQL_SSH=$OPENSQL_HOST,
# PATRONI_URL=http://$OPENSQL_HOST:8008, PATRONI_LOG=/home/opensql/logs/patroni.log, API_PORT=18000
OPENSQL_HOST=<vm-ip> \
OPENSQL_SSH=<ssh-host> \
DATABASE_URL="postgresql://postgres:pg_password@<vm-ip>:6432/opensql" \
PATRONI_URL="http://<vm-ip>:8008" \
PATRONI_LOG="/home/opensql/logs/patroni.log" \
API_PORT=18000 \
bash scripts/demo_recovery.sh
```

SSH 공개키 인증과 원격 호스트의 비밀번호 없는 `sudo`가 필요합니다. 데모는 postmaster 부모
프로세스에 `SIGKILL`을 한 번 보내고 Patroni의 자동 재기동, 앱 연결 예외와 재접속, 미처리 잡 재개,
정합성 수렴을 확인합니다.

**검증한 것은 DB 프로세스 장애 자동 복구와 애플리케이션의 재연결·잡 재개·정합성 수렴입니다.**
노드 사망은 복구되지 않으며, 이는 사무국 지시에 따른 Single 구성의 제약입니다. HA 설계는
유지하되 노드 승격은 검증하지 못했습니다 (ADR-020).
