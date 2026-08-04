# Step 0: 마이그레이션 러너

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ADR.md` — **ADR-005**(번호 붙은 raw SQL + 소형 러너, ORM 마이그레이션 도구 금지), **ADR-012**(러너는 `app/migrations.py`에 두고 **API 서버 startup에서만** 호출 / `db.py`는 import 부작용 없음), **ADR-007**(로컬은 `pgvector/pgvector:pg17` 단일 컨테이너)
- `/docs/ARCHITECTURE.md` — "디렉토리 구조" 절에서 `migrations/`와 `app/migrations.py`의 위치
- **M0 산출물**: `/backend/app/config.py`, `/backend/app/db.py`, `/backend/app/main.py`, `/backend/tests/conftest.py`, `/backend/pyproject.toml`
- `/docker-compose.yml`, `/.env.example` — 로컬 DB 자격증명과 포트
- `/scripts/hooks/tdd-guard.sh` — `app/migrations.py`는 테스트를 요구하는 파일이다 (`main.py`·`config.py`·`db.py`와 달리 면제 목록에 없다)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

이 phase의 나머지 step은 전부 **실제 DB에 마이그레이션을 적용한 상태**를 전제로 테스트한다. 러너가 없으면 트리거도 워커도 검증할 수 없으므로 이 step이 가장 먼저다.

M0에서 러너를 만들지 않은 이유는 그때 적용할 SQL이 없어 테스트가 빈 껍데기가 되기 때문이다. 이 step은 **임시 SQL 픽스처**(`tmp_path`에 직접 만든 `.sql` 파일)로 러너를 검증한다. 실제 `001_extensions.sql`·`002_tables.sql`은 **다음 step에서 만든다.**

이 step은 **테스트가 실제 PostgreSQL 컨테이너에 붙는 첫 step**이기도 하다. 그래서 뒤따르는 모든 DB 테스트가 쓸 픽스처(테스트 전용 DB 생성·초기화)를 여기서 함께 만든다.

## 작업

### 1. `backend/tests/conftest.py` — 테스트 DB 픽스처 추가

기존 `_clear_settings_cache`·`client` 픽스처는 **그대로 두고** 아래를 추가한다.

```python
@pytest.fixture(scope="session")
def test_dsn() -> str:
    """테스트 전용 DB를 만들고 그 DSN을 반환한다."""

@pytest.fixture
def clean_db(test_dsn: str) -> str:
    """스키마가 비워진 테스트 DB의 DSN. 매 테스트마다 초기화된다."""
```

핵심 규칙:

- **개발 DB를 건드리지 마라.** `Settings.database_url`의 DSN에서 **데이터베이스 이름만** `openarchive_test`로 바꿔 쓴다. psycopg의 `conninfo_to_dict()` / `make_conninfo()`를 쓰면 문자열을 직접 파싱하지 않아도 된다. 호스트·포트·자격증명은 그대로 물려받으므로 실 클러스터로 전환해도 코드가 그대로 동작한다.
- `test_dsn`은 세션 시작 시 관리 연결(원래 DSN, `autocommit=True`)로 `CREATE DATABASE openarchive_test`를 실행한다. 이미 있으면 그대로 쓴다.
- `clean_db`는 매 테스트 전에 `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`으로 테스트 DB를 완전히 비운다. **테스트 간 격리를 단순하게 유지하는 것이 목적이다.** 빈 스키마라 비용이 거의 없다.
- 픽스처는 **동기**로 작성한다 (`psycopg.connect`). 러너만 async이므로 테스트 함수 쪽에서 `await`하면 된다.
- **DB에 접속할 수 없으면 명확한 메시지와 함께 실패시켜라.** `pytest.skip`으로 넘기지 마라. 이유: 이 프로젝트는 트리거·`SKIP LOCKED`·`vector` 연산자를 실제 DB에서만 검증할 수 있고, 조용히 skip되면 "테스트가 통과했다"는 신호가 거짓이 된다 (`CLAUDE.md` 개발 프로세스).

### 2. `backend/app/migrations.py` — 러너

```python
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

async def run_migrations(dsn: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """아직 적용되지 않은 마이그레이션을 파일명 순으로 적용하고, 적용한 파일명을 반환한다."""
```

이력 테이블은 **러너가 직접 만든다** (마이그레이션 파일로 만들지 않는다 — 자기 자신을 기록할 테이블이므로 부트스트랩이 필요하다).

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename   text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
```

핵심 규칙 — 반드시 지켜라:

- **파일 하나 = 트랜잭션 하나.** SQL 실행과 `schema_migrations` INSERT가 **같은 트랜잭션**에 들어가야 한다. 이유: SQL은 적용됐는데 이력이 빠지면(또는 그 반대) 다음 실행이 같은 파일을 다시 적용해 깨진다.
- **정렬은 파일명 사전순.** 파일명은 `001_`, `002_` 형태의 3자리 zero-padding을 규약으로 하므로 사전순이 곧 번호순이다. 파일명에서 숫자를 파싱해 정렬하는 코드를 따로 만들지 마라.
- **이미 적용된 파일은 건너뛴다** (멱등). 두 번째 실행은 아무것도 적용하지 않고 빈 리스트를 반환해야 한다.
- 대상은 `*.sql` 파일만. 하위 디렉토리는 훑지 않는다.
- **실패하면 예외를 그대로 올려라.** 삼키고 계속 진행하지 마라. 부분 적용된 스키마 위에서 애플리케이션이 도는 것이 조용히 실패하는 것보다 훨씬 위험하다.
- 커넥션은 `psycopg.AsyncConnection.connect(dsn)`으로 **직접 열고 닫는다.** `app.db.get_pool()`을 쓰지 마라. 이유: 마이그레이션은 기동 시 1회성 작업이고, 풀의 수명주기와 얽히면 `db.py`의 "import 부작용 없음" 성질을 흐린다 (ADR-012).
- **advisory lock이나 분산 락을 만들지 마라.** 이유: ADR-012가 실행 주체를 API 서버 하나로 고정했으므로 경쟁 자체가 발생하지 않는다. 데모 규모에서 과잉이다.

### 3. `backend/app/main.py` — startup 배선

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_migrations(get_settings().database_url)
    yield

app = FastAPI(title="OpenArchive API", lifespan=lifespan)
```

- `/api/health` 핸들러는 그대로 둔다.
- **커넥션 풀을 열거나 `close_pool()`을 부르지 마라.** 이유: 이 phase에는 DB를 쓰는 API 라우터가 없다. 풀 수명주기는 API가 실제로 DB를 조회하기 시작하는 phase에서 붙인다.
- **`tests/conftest.py`의 `client` 픽스처를 `with TestClient(app) as c:` 형태로 바꾸지 마라.** 이유: `TestClient`는 컨텍스트 매니저로 쓸 때만 lifespan을 실행한다. 지금처럼 그냥 인스턴스를 반환하면 lifespan이 돌지 않으므로 `test_main.py`가 DB 없이도 통과한다. 바꾸면 앱 테스트 전체가 DB에 묶인다.

### 4. 테스트 — 먼저 작성한다

**구현보다 테스트를 먼저 작성하고 실패를 확인한 뒤 구현하라** (TDD). `assert True`나 예외만 삼키는 테스트는 금지다.

`backend/tests/test_migrations.py`에 최소 아래를 검증한다. SQL 픽스처는 `tmp_path`에 직접 쓴다.

1. **번호순 적용** — `001_*.sql`이 테이블을 만들고 `002_*.sql`이 그 테이블에 컬럼을 추가하도록 픽스처를 구성한다. 순서가 뒤집히면 002가 실패하므로, 통과 자체가 순서를 증명한다. 반환값도 `["001_...", "002_..."]` 순서여야 한다.
2. **재실행 멱등** — 같은 디렉토리로 두 번 호출하면 두 번째 반환값이 빈 리스트이고, `schema_migrations` 행 수가 늘지 않는다.
3. **이력 기록** — 적용 후 `schema_migrations`에 두 파일명이 있고 `applied_at`이 채워져 있다.
4. **부분 적용 방지** — 한 파일 안에 유효한 문장과 잘못된 문장을 함께 넣는다. 예외가 발생하고, **앞부분 문장의 효과가 DB에 남지 않으며**, 그 파일이 `schema_migrations`에도 없어야 한다.
5. **빈 디렉토리** — 예외 없이 빈 리스트를 반환한다.

## Acceptance Criteria

```bash
docker compose up -d              # 프로젝트 루트에서. db가 healthy 상태여야 한다
docker compose ps

cd backend
.venv/bin/ruff check .
.venv/bin/pytest                  # 기존 14개 + 신규 테스트 전부 통과

# import 부작용 부재는 여전히 유지되어야 한다 (ADR-012)
.venv/bin/python -c "import app.db; import app.migrations; print('import ok')"

cd ..
bash scripts/check.sh             # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 러너가 `app/migrations.py`에 있고 `app/db.py`에 있지 않은가? (ADR-012)
   - 파일 적용과 이력 기록이 같은 트랜잭션인가?
   - 테스트가 실제 컨테이너에 붙는가? Mock·SQLite·인메모리로 대체하지 않았는가?
   - 테스트 DB가 개발 DB(`openarchive`)와 분리되어 있는가?
3. 결과에 따라 `phases/m1-db-layer/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **`run_migrations`의 정확한 시그니처와 `conftest.py`에 추가한 픽스처 이름을 반드시 포함시켜라.** 이후 모든 step이 이 둘을 쓴다.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 (컨테이너 기동 불가 등) → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **마이그레이션 SQL 파일(`001_*.sql` 등)을 만들지 마라.** 이유: 스키마는 다음 step의 범위다. 이 step은 러너만 만들고 임시 픽스처로 검증한다.
- **Alembic 등 마이그레이션 도구나 ORM을 추가하지 마라.** 이유: ADR-005.
- **`app/db.py`를 수정하지 마라.** 이유: 러너는 자체 커넥션을 열며 풀과 무관하다 (ADR-012).
- **워커·MCP 서버에서 `run_migrations`를 호출하는 코드를 만들지 마라.** 이유: 세 프로세스가 같은 마이그레이션을 경쟁 실행한다 (ADR-012).
- **DB에 접속할 수 없을 때 테스트를 `skip`하거나 통과시키지 마라.** 이유: 조용한 skip은 "검증했다"는 거짓 신호를 만든다.
- **`clean_db`가 개발 DB(`openarchive`)의 스키마를 드롭하지 않도록 하라.** 이유: 대상 DB 이름을 잘못 계산하면 개발 데이터가 사라진다. 픽스처가 접속하는 DB 이름을 테스트로 확인하라.
- **`app/worker.py`·`app/services/*`·`app/embeddings/*`를 만들지 마라.** 이유: 후속 step의 범위다.
- 기존 테스트를 깨뜨리지 마라. `scripts/test_execute.py`와 M0의 백엔드 테스트가 통과 상태를 유지해야 한다.
