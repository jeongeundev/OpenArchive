# Step 4: mcp-server

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — 최상단 구성도와 디렉토리 구조(`backend/mcp_server/server.py`)
- `/docs/ADR.md` — **ADR-008**(MCP는 stdio + services 직접 import), **ADR-012**(마이그레이션 실행 주체), **ADR-015**(생성 LLM 미탑재), **ADR-003**(임베딩 프로바이더)
- `/docs/PROJECT_CONTEXT.md` — MCP가 대회 규정에서 갖는 의미
- `backend/app/db.py` — **풀은 `get_pool()` 시점에 만들어지고, 여는 것은 호출부의 책임이다.** 주석에 MCP 서버가 이 규약의 이유로 적혀 있다
- `backend/app/embeddings/__init__.py` — `get_provider()`
- `backend/app/config.py` — 설정 추가 위치
- `backend/app/services/search.py` — `search_documents(conn, provider, ...)`
- `backend/app/services/documents.py` — `get_document`, `list_documents`
- `backend/app/main.py` — API 서버가 프로바이더·풀·마이그레이션을 어떻게 다루는지 (MCP는 마이그레이션을 실행하지 **않는다**)
- `backend/pyproject.toml` — 의존성과 패키지 include 설정

**구현 전에 설치된 `mcp` 패키지의 FastMCP API를 확인하라.** 버전에 따라 툴 등록·lifespan·`list_tools` 시그니처가 다르다. 추측으로 쓰지 말고 `python -c "import mcp; print(mcp.__file__)"`로 설치 경로를 찾아 실제 API를 읽어라.

## 작업

AI 에이전트에 사내 문서의 **근거를 공급**하는 MCP 서버를 만든다. 대회 규정 [별표2]가 적극 허용·권장하는 유일한 기술이다.

### 1) 의존성 (`backend/pyproject.toml`)

- `dependencies`에 `"mcp"`를 추가한다
- `[tool.setuptools.packages.find]`의 `include`에 `"mcp_server*"`를 추가하고, "배포되는 것은 app 패키지뿐"이라는 기존 주석을 실제와 맞게 고친다. 이유: MCP 클라이언트가 서버를 기동할 때 작업 디렉토리를 통제하기 어려우므로, 설치된 패키지로 `python -m mcp_server.server`가 어디서든 동작해야 한다

### 2) 서버 (`backend/mcp_server/__init__.py`, `backend/mcp_server/server.py`)

FastMCP + **stdio transport**, 단일 파일. `app.services`를 **직접 import**하고 HTTP API를 경유하지 않는다 (ADR-008).

툴 3개:

```python
async def search_documents(query: str, tags: list[str] | None = None,
                           content_type: str | None = None, k: int = 10) -> dict
async def get_document(document_id: str) -> dict
async def list_documents(tag: str | None = None, status: str | None = None) -> dict
```

**툴 함수 정의와 등록을 분리하라.** 데코레이터를 함수 위에 얹지 말고, 모듈 하단에서 `mcp.tool()(search_documents)` 형태로 등록한다. 이유: 테스트가 원 함수를 그대로 호출할 수 있어야 하고, SDK 버전이 데코레이터 반환값을 바꿔도 테스트가 깨지지 않는다.

각 툴은 풀에서 커넥션을 빌려 서비스를 호출한다:

```python
async with get_pool().connection() as conn:
    hits = await search_documents_service(conn, provider, query=query, ...)
```

### 3) 반환 형식 — 근거로 쓸 수 있어야 한다

`search_documents`의 각 항목에 **발췌·출처·기준 버전**을 함께 담는다.

```
{
  "items": [
    {
      "document_id": "...", "title": "...", "filename": "...",
      "content_type": "md", "tags": [...],
      "excerpt": "<매칭된 청크 본문>", "chunk_index": 3,
      "score": 0.82, "based_on_version": 2
    }
  ]
}
```

`based_on_version`은 `document_chunks.version`이다 — "지금 이 발췌가 몇 번 버전에서 나왔는가"를 답한다. `SearchHit`에 해당 필드가 없으면 **`services/search.py`의 `SearchHit`과 `SEARCH_SQL`에 `c.version`을 추가**하라(REST 응답 모델은 건드리지 않아도 된다). 서비스를 고쳤다면 `backend/tests/test_search.py`에 검증을 추가한다.

`get_document`는 본문·텍스트 버전 목록·`chunk_count`·`chunk_version`을 담는다. `list_documents`는 요약 목록을 담는다.

각 툴에 **한국어 docstring**을 달아라. MCP 클라이언트가 이 설명을 보고 툴을 고른다. "무엇을 반환하는가"와 "언제 쓰는가"를 한 줄씩 적는다.

### 4) 사용자 컨텍스트 — 기본은 public만

MCP 클라이언트에는 `X-User-Id` 헤더가 없다. `app/config.py`의 `Settings`에 다음을 추가한다:

```python
mcp_user_id: str | None = None   # 환경변수 MCP_USER_ID
```

툴은 이 값을 `user_id`로 서비스에 넘긴다. **기본값이 `None`이므로 설정하지 않으면 public 문서만 보인다** — 안전한 쪽이 기본이다. 이 결정은 step 7에서 ADR로 기록한다.

### 5) 프로세스 수명

- 풀은 `get_pool()` + `await pool.open()`으로 명시적으로 연다. FastMCP가 lifespan 훅을 제공하면 그것을 쓰고, 없으면 서버 기동 직후 한 번 여는 경로를 만든다
- 프로바이더는 `get_provider()`로 한 번 만들어 재사용한다. **질의 임베딩과 문서 임베딩이 같은 프로바이더여야 한다** — 벡터 공간이 다르면 검색이 에러 없이 무의미해진다
- **마이그레이션을 실행하지 마라.** 스키마는 준비된 것으로 가정한다. 이유: 세 프로세스가 같은 마이그레이션을 경쟁 실행하게 된다 (ADR-012)
- `python -m mcp_server.server`로 기동되도록 `main()`과 `if __name__ == "__main__":`을 둔다

## 테스트

`backend/tests/test_mcp_server.py`를 **먼저** 작성한다.

> 파일명 주의: 이슈 본문은 `test_mcp.py`라고 적었지만 **`test_mcp_server.py`를 쓴다.** tdd-guard 훅이 `server.py`에 대해 `test_*server*.py`를 찾으므로 `test_mcp.py`로는 매치되지 않는다.

최소한 아래를 덮어야 한다.

- 등록된 툴이 정확히 `search_documents`·`get_document`·`list_documents` 3개다
- **`search_documents` 툴의 결과가 REST와 일치한다** — 같은 DB·같은 질의로 `services.search.search_documents()`를 직접 호출한 결과와 `document_id`·순서가 같은지 비교한다. 이것이 ADR-008의 존재 이유이므로 반드시 검증한다
- 각 항목에 `excerpt`·`title`·`based_on_version`이 채워져 있다
- **`MCP_USER_ID`가 없으면 private 문서가 결과에 없고**, 소유자로 설정하면 보인다 (`search_documents`·`get_document`·`list_documents` 모두)
- `get_document`가 존재하지 않는 ID에 대해 예외를 던지거나 오류를 반환한다 (조용히 빈 결과를 주지 않는다)

DB는 실제 컨테이너를 쓴다. `migrated_db` 픽스처로 DSN을 얻고 `DATABASE_URL`을 monkeypatch한 뒤 풀을 연다. 테스트 종료 시 `close_pool()`로 정리한다 — 풀은 모듈 전역이라 다음 테스트로 DSN이 누수된다.

## Acceptance Criteria

```bash
cd backend
source .venv/bin/activate
pip install -e ".[dev]"                  # mcp 의존성 설치
pytest tests/test_mcp_server.py -q       # 전부 통과
pytest -q                                # 기존 테스트도 통과
ruff check .                             # 린트 통과
python -m mcp_server.server < /dev/null  # 기동 후 stdin EOF로 즉시 종료. 트레이스백이 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - MCP 서버가 HTTP API를 경유하지 않고 `app.services`를 직접 import하는가? (ADR-008)
   - 검색 SQL이 `services/search.py` 한 곳에만 있는가? MCP용 쿼리를 따로 만들지 않았는가?
   - 마이그레이션을 실행하지 않는가? (ADR-012)
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m4-mcp-gateway/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (실행 커맨드와 환경변수 이름 포함 — step 7의 문서화에 쓴다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

> Claude Desktop/Code에 실제로 등록해 동작을 확인하는 것은 **사람이 하는 수동 검증**이다. 이 step에서 시도하지 말고 blocked로도 만들지 마라. step 7이 등록 절차를 문서로 남기고, phase 종료 후 사람이 확인한다.

## 금지사항

- MCP 서버에서 HTTP로 자기 API를 호출하지 마라. 이유: 계층 중복과 장애 지점만 늘고, 결과 일치 보장이 깨진다 (ADR-008)
- MCP 전용 검색 SQL을 새로 쓰지 마라. 이유: REST와 MCP 결과가 항상 일치해야 한다
- 답변을 생성하거나 요약하는 툴을 만들지 마라. 이유: 이 플랫폼은 생성 LLM을 탑재하지 않는다. 우리 책임은 근거 공급까지다 (ADR-015)
- 상용 임베딩 API를 붙이지 마라. 이유: 대회 규정 위반이다 (ADR-003)
- `import` 시점에 DB에 접속하거나 풀을 열지 마라. 이유: `app.db`가 import 부작용 없음을 전제로 설계되어 있다 (ADR-012)
- 사용자 컨텍스트를 툴 인자로 받지 마라(`user_id` 파라미터를 툴 시그니처에 노출하지 마라). 이유: 클라이언트가 임의의 사용자를 사칭해 private 문서를 읽을 수 있게 된다
- 마이그레이션 파일을 건드리지 마라
- 기존 테스트를 깨뜨리지 마라
