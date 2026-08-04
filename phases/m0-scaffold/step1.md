# Step 1: 백엔드 스캐폴드

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "디렉토리 구조" 절(백엔드 트리 전체), "임베딩 프로바이더" 절, "API 설계" 절
- `/docs/ADR.md` — **ADR-006**(DB 접속은 OpenProxy 단일 엔드포인트, DSN은 환경변수로만), **ADR-012**(마이그레이션은 API 서버만 실행 / `db.py`는 import 부작용 없음), **ADR-003**(임베딩 모델 제약), **ADR-008**(MCP 서버가 `app.services`를 직접 import)
- `/scripts/check.sh` — **`backend/.venv/bin/pytest`와 `.venv/bin/ruff`를 직접 호출한다.** 가상환경을 만들지 않으면 통합 검증이 실패한다
- `/scripts/hooks/tdd-guard.sh` — 어떤 파일이 테스트를 요구하고 어떤 파일이 면제인지
- **이전 step 산출물**: `/docker-compose.yml`, `/.env.example` — DB 자격증명과 포트를 여기서 확인해 `config.py` 기본값에 반영한다

## 배경

이 step은 백엔드의 **뼈대만** 만든다. 문서 CRUD·검색·임베딩·워커는 후속 phase 범위다. 목표는 "임포트되고 기동되고 테스트가 도는 최소 상태"다.

`app/migrations.py`(마이그레이션 러너)는 **이 step에서 만들지 않는다.** 적용할 SQL이 아직 없어 검증 대상이 없고, 그러면 tdd-guard가 요구하는 테스트가 빈 껍데기가 되기 때문이다. 러너는 실제 마이그레이션 SQL이 생기는 후속 phase에서 TDD로 만든다.

## 작업

### 1. `backend/pyproject.toml`

```toml
[project]
name = "openarchive-backend"
requires-python = ">=3.12"
```

의존성은 아래로 고정한다. **여기에 없는 패키지를 추가하지 마라.**

| 구분 | 패키지 | 용도 |
|---|---|---|
| 런타임 | `fastapi` | API 프레임워크 |
| 런타임 | `uvicorn[standard]` | ASGI 서버 (`uvicorn app.main:app --reload`) |
| 런타임 | `psycopg[binary,pool]` | DB 드라이버 + 커넥션 풀 |
| 런타임 | `pydantic-settings` | 환경변수 기반 설정 |
| 런타임 | `pypdf` | PDF 텍스트 추출 (후속 phase에서 사용) |
| 런타임 | `python-docx` | DOCX 텍스트 추출 (후속 phase에서 사용) |
| dev | `pytest` | 테스트 러너 |
| dev | `pytest-asyncio` | async 테스트 |
| dev | `httpx` | FastAPI `TestClient` 의존성 |
| dev | `ruff` | 린트 |

- 빌드 백엔드는 `setuptools`. `pip install -e ".[dev]"`가 동작해야 하므로 패키지 탐색 범위를 `app`으로 한정한다 (`tests`가 설치 대상에 들어가지 않도록).
- `[tool.ruff]`에 `target-version`과 `line-length`를 명시한다. lint 룰은 기본 세트에서 크게 벗어나지 마라 — 지금 코드가 거의 없어 과한 규칙은 검증되지 않는다.
- `[tool.pytest.ini_options]`에 `testpaths = ["tests"]`와 asyncio 모드를 설정한다.

### 2. 패키지 구조

```
backend/
├── pyproject.toml
├── migrations/          # .gitkeep 만. SQL은 후속 phase
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI 앱 조립 + GET /api/health
│   ├── config.py        # pydantic-settings
│   ├── db.py            # 커넥션 풀 — import 부작용 없음
│   ├── api/__init__.py
│   ├── services/__init__.py
│   └── embeddings/__init__.py
└── tests/
    ├── conftest.py
    ├── test_main.py
    ├── test_config.py
    └── test_db.py
```

`api/`·`services/`·`embeddings/` 안에는 `__init__.py` **외의 파일을 만들지 마라** (금지사항 참조).

### 3. 시그니처

**`app/config.py`**

```python
class Settings(BaseSettings):
    database_url: str          # 기본값은 docker-compose.yml의 로컬 DSN과 일치시킨다
    embedding_provider: Literal["local", "fake"]   # 기본값 "fake"
    # model_config: env_file=".env", 알 수 없는 키는 무시

def get_settings() -> Settings: ...   # 캐시해 매 호출마다 재파싱하지 않는다
```

`database_url`에 기본값을 두는 것은 ADR-006 위반이 아니다. ADR-006이 금지하는 것은 **멀티호스트 DSN과 `target_session_attrs`를 코드에 박는 것**이며, 단일 엔드포인트 DSN을 환경변수로 덮어쓸 수 있는 기본값은 "clone 후 바로 실행 가능"이라는 이 phase의 목적에 부합한다.

**`app/db.py`**

```python
def get_pool() -> AsyncConnectionPool: ...    # 최초 호출 시 생성, 이후 재사용
async def close_pool() -> None: ...           # 앱 종료 시 정리
```

**핵심 규칙 — 반드시 지켜라**: 모듈 최상위에서 `AsyncConnectionPool(...)`을 인스턴스화하지 않는다. `import app.db`만으로는 커넥션이 열리지 않아야 한다. 이유: MCP 서버가 `app.services`를 직접 import하고(ADR-008), 워커도 같은 패키지를 쓴다. import가 곧 접속이면 세 프로세스가 의도치 않게 풀을 열게 된다 (ADR-012).

psycopg_pool은 생성 시 자동으로 열리므로, 풀을 만들 때 자동 open을 끄고 명시적으로 열어라.

**`app/main.py`**

```python
app = FastAPI(...)

@app.get("/api/health")
def health() -> dict: ...    # {"status": "ok"}
```

- 이 step의 앱은 **DB에 연결하지 않는다.** lifespan에서 풀을 열거나 마이그레이션을 실행하지 마라. 이유: DB 없이도 앱이 기동되고 테스트가 돌아야 하며, 스키마 적용은 실제 마이그레이션 SQL이 생기는 후속 phase에서 붙인다.
- 라우터 파일을 만들어 `include_router`하지 마라. `/api/health`는 `main.py`에 직접 둔다.

### 4. 테스트 — 먼저 작성한다

**구현보다 테스트를 먼저 작성하고, 실패를 확인한 뒤 구현하라** (TDD). `assert True`나 예외만 삼키는 테스트는 금지다.

- `tests/conftest.py` — FastAPI `TestClient` 픽스처
- `tests/test_main.py` — `GET /api/health`가 200과 `{"status": "ok"}`를 반환한다
- `tests/test_config.py` — 환경변수로 `DATABASE_URL`·`EMBEDDING_PROVIDER`가 주입되는지(monkeypatch), `embedding_provider` 기본값이 `"fake"`인지, 허용되지 않은 provider 값이 `ValidationError`를 일으키는지
- `tests/test_db.py` — **이 step의 핵심 검증**: `DATABASE_URL`이 설정되지 않은 상태에서도 `app.db` import가 성공하고, `get_pool()`을 호출하기 전에는 풀 객체가 생성되지 않는다. 모듈을 새로 import했을 때 커넥션이 열리지 않음을 확인하라 (`importlib.reload` 활용)

`test_db.py`는 DB 컨테이너 없이 통과해야 한다 — import 부작용의 부재를 검증하는 것이지 접속을 검증하는 것이 아니다.

### 5. 가상환경 생성

`check.sh`가 `backend/.venv/bin/*`를 직접 호출하므로 **가상환경을 반드시 만들어 의존성을 설치하라.** `.venv/`는 이미 `.gitignore` 대상이다.

## Acceptance Criteria

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/pytest

# DB 없이 앱이 임포트되는지 (import 부작용 부재 확인)
.venv/bin/python -c "import app.db; import app.main; print('import ok')"

cd ..
bash scripts/check.sh      # backend는 실행되고, frontend는 '건너뜀'이 정상
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ARCHITECTURE.md`의 백엔드 디렉토리 구조와 일치하는가?
   - `app/db.py`가 import 시 커넥션을 열지 않는가? (ADR-012)
   - DSN이 코드에 하드코딩되지 않고 `config.py`를 통해 환경변수로 주입되는가? (ADR-006)
   - 테스트가 실제 동작을 검증하는가, 아니면 통과만 시키는 껍데기인가?
3. 결과에 따라 `phases/m0-scaffold/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (생성한 모듈 경로와 `/api/health` 존재를 포함시켜라 — 다음 step의 프론트엔드 rewrites 대상이다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **`app/migrations.py`를 만들지 마라.** 이유: 적용할 SQL이 없어 검증 대상이 없고, 그러면 tdd-guard가 요구하는 테스트가 빈 껍데기가 된다. 러너는 마이그레이션 SQL이 생기는 후속 phase에서 TDD로 만든다.
- **`app/api/`·`app/services/`·`app/embeddings/` 안에 `__init__.py` 외의 파일을 만들지 마라** (`documents.py`, `search.py`, `system.py`, `base.py`, `fake.py` 등). 이유: 이 step은 구현 로직을 넣지 않는다. 내용 없는 모듈은 tdd-guard가 요구하는 테스트를 무의미하게 만든다.
- **`app/worker.py`와 `mcp_server/`를 만들지 마라.** 이유: 각각 임베딩 파이프라인·MCP phase의 범위다.
- **`sentence-transformers`를 의존성에 넣지 마라.** 이유: BGE-M3 모델이 약 2GB이고, 이 step에는 임베딩 코드가 없다. 임베딩 phase에서 추가한다.
- **상용 임베딩 API 클라이언트(openai, cohere 등)를 넣지 마라.** 이유: 대회 규정이 API 전용 모델 사용을 금지한다 (ADR-003).
- **`app/db.py` 모듈 최상위에서 풀을 인스턴스화하지 마라.** 이유: ADR-012 — import가 곧 접속이면 MCP·워커·API 세 프로세스가 각각 풀을 연다.
- **`main.py`의 lifespan에서 DB에 연결하거나 마이그레이션을 실행하지 마라.** 이유: 이 step에는 적용할 스키마가 없고, DB 없이 테스트가 돌아야 한다.
- **DSN을 코드에 문자열로 박아 넣지 마라** (`config.py`의 기본값 한 곳은 예외). 멀티호스트 DSN·`target_session_attrs`는 어디에도 쓰지 마라. 이유: ADR-006.
- **ORM(SQLAlchemy 등)이나 마이그레이션 도구(Alembic 등)를 추가하지 마라.** 이유: 스키마 변경은 `backend/migrations/`의 번호 붙은 raw SQL로만 한다.
- 기존 테스트를 깨뜨리지 마라. `scripts/test_execute.py`가 이미 존재하며 통과 상태를 유지해야 한다.
