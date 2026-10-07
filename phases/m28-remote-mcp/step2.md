# Step 2: mount-mcp

step 1의 `remote_mcp`를 API 앱(`backend/openarchive/main.py`)의 `/mcp`에 붙이고, **실제 MCP SDK 클라이언트**로 명세서 시험항목 전부를 끝에서 끝까지 고정한다.

## 공통 배경 — m28-remote-mcp 설계 결정 (모든 step 같음)

이슈 #188 원격 MCP다. 근거 ADR-056(채택 2026-10-05), 주체 계약 ADR-025·036, 토큰 ADR-034, 공유 토큰 ADR-044 「공유」, 감사 ADR-055. 지금 MCP는 `backend/openarchive/mcp_server/server.py` 하나뿐이고 stdio로만 돈다 — 도구 4개(`search_documents`·`get_document`·`list_documents`·`create_document`)가 모두 `get_settings().mcp_user_id`로 주체를 정한다. 이 phase는 같은 도구를 **API 서버의 `/mcp`(Streamable HTTP)** 에도 노출하고, 주체를 **Bearer API 토큰**에서만 정한다. stdio는 그대로 남는다.

결정 (2026-10-07 사용자 승인):

- **D1 배치** — 원격 MCP는 API 앱(`openarchive/main.py`)에 붙는다. `openarchive serve`(uvicorn `openarchive.main:app`)를 띄우면 함께 뜬다. 클라이언트 주소는 `http://<서버>:8000/mcp`. 별도 프로세스·포트를 만들지 않는다. API의 DB 풀과 **예열된 임베딩 프로바이더(`app.state.provider`)를 그대로 쓴다** — `server.py`가 import 때 만드는 모듈 전역 `provider`를 원격 경로에서 쓰면 BGE-M3가 한 프로세스에 두 번 올라간다(약 2GB).
- **D2 세션 모드** — `stateless_http=True`, `json_response=True`. 도구는 요청 하나 → 응답 하나뿐이고 서버→클라이언트 알림이 없다. 매 요청 토큰을 검증하므로 세션 상태가 필요 없고 서버 재시작에도 클라이언트가 끊기지 않는다.
- **D3 인증** — SDK의 `token_verifier`/`AuthSettings`를 **쓰지 않는다**(OAuth issuer·보호 리소스 메타데이터를 노출해 ADR-056이 기각한 OAuth 인가 서버를 있는 것처럼 알린다). 대신 `services/auth.validate_token`을 부르는 ASGI 미들웨어가 `/mcp` 앞을 지킨다. `Authorization: Bearer <토큰>`이 없거나, 스킴이 Bearer가 아니거나, 토큰이 틀렸거나 폐기됐으면 **401 + `WWW-Authenticate: Bearer`**. 토큰 해석은 REST(`api/deps.current_user`)와 같은 함수 하나다 — SHA-256 대조·행 삭제 폐기·scope·공유 주체를 새로 구현하지 않는다.
- **D4 주체 해석** — `FastMCP` 인스턴스를 transport마다 하나씩 둔다(도구 본체는 공유). **stdio 인스턴스**는 지금처럼 `MCP_USER_ID`로 주체를 정하고 풀을 여닫는 lifespan을 가진다. **원격 인스턴스**는 미들웨어가 넘긴 토큰 주체만 읽고, 주체가 비어 있으면 에러다 — `MCP_USER_ID`로 떨어지는 경로가 구조상 없다(ADR-056 결정 3). 원격 인스턴스에는 풀 lifespan을 주지 않는다: SDK는 lifespan을 **세션(stateless면 요청)마다** 들어갔다 나오므로, 지금 lifespan을 주면 요청마다 `close_pool()`이 불린다.
- **D5 Host/Origin 검사** — SDK의 DNS rebinding 보호를 **끈다**(`TransportSecuritySettings(enable_dns_rebinding_protection=False)`를 명시). `FastMCP(host=...)` 기본값이 `127.0.0.1`이라 아무것도 안 넘기면 보호가 자동으로 켜지고 허용 Host가 localhost뿐이어서, 다른 PC가 `Host: 192.168.x.x:8000`로 붙으면 거부된다. DNS rebinding은 인증 없는 로컬 서버를 노리는 공격이며, 모든 요청이 Bearer를 요구하고 CORS를 열지 않아(브라우저가 교차 오리진으로 `Authorization`을 싣지 못한다) 공격 전제가 성립하지 않는다. Host 허용 목록을 설정으로 받으면 "주소와 토큰만 등록"이라는 명세서 계약이 깨진다.
- **D6 scope와 감사** — 읽기 도구 3개는 모든 유효 토큰(사용자 `read`·`read_write`, 공유 토큰)에 열린다. `create_document`는 `read_write` **사용자** 토큰만 — 소유자는 토큰 주인. `read` 토큰·공유 토큰의 호출은 **DB에 닿기 전에** "쓰기 권한이 필요합니다."로 거부되어 문서가 생기지 않는다(REST 403 문구와 같다). 원격 생성은 같은 트랜잭션에서 `set_actor(conn, actor=<토큰 주인 사용자명>, via="mcp")`를 부른다 — 경로 값이라 stdio와 같은 `mcp`이고 CHECK 목록에 이미 있어 마이그레이션이 없다. 행위자를 안 걸면 에러 없이 `actor` NULL로 남으므로 테스트로 고정한다. 읽기는 감사 대상이 아니다.
- **D7 `needs_vm=true`** — 머지 전 실사용 점검(실제 MCP 클라이언트 등록, HA VIP 경유)에서 멈춘다. 이 phase의 step 세션은 VM에 손대지 않는다.

**바꾸지 않는 것**: 도구 개수·이름·인자·응답 모양(ADR-036 — 원격이 생겼다고 편집·삭제 도구를 더하지 않는다), 열람 술어·검색 SQL(서비스가 `ensure_visible`로 검증, ADR-018·027), 멱등키(`create_document` 호출마다 키 하나, 백오프 재시도가 같은 키를 씀 — ADR-047), DB 백오프(`with_backoff` — ADR-048), DB 스키마. 토큰 만료·마지막 사용 시각은 #199 범위다 — `validate_token`을 재사용하므로 그쪽이 들어오면 원격 MCP도 자동으로 따른다. 여기서 구현하지 않는다.

### 기능명세서 시험항목 — 문구가 구현 계약이다 (글자 그대로 지킨다)

정본은 제출본 `notes/contest/submission/functional-spec-submit.md`(2026-10-07 제출, 로컬 파일)다. 이슈 #188 본문 표는 제출 전 초안이라 사람 이름(bob/carol)이 다르고 2항목이 더 있다 — 더 있는 2항목(폐기 토큰 401, `read_write` 생성)도 함께 지킨다.

| 중분류 | 소분류 | 시험 내용 |
|---|---|---|
| 원격(HTTP) | 접속·토큰 인증 | [Pre-condition] 설정 화면에서 kim의 API 토큰 발급 / MCP 클라이언트에 `http://localhost:8000/mcp`와 `Authorization: Bearer <토큰>`만 등록하면(DB 접속 정보 없이) 4개 도구가 노출되고 `search_documents`가 결과를 반환함 |
| 원격(HTTP) | 접속·토큰 인증 | 토큰 없이 `/mcp`에 접속하면 401로 거부됨 |
| 원격(HTTP) | 접속·토큰 인증 | 원격 MCP의 검색 결과는 토큰 주인의 열람 범위를 따름 — kim의 토큰으로는 lee의 「제한」 문서가 나오지 않음 |
| 원격(HTTP) | 토큰 범위·공유 토큰 | `read` 토큰으로 `create_document`를 호출하면 거부되고 문서가 생기지 않음 |
| 원격(HTTP) | 토큰 범위·공유 토큰 | 공유 토큰으로 원격 MCP에 접속하면 공유에 넣은 문서만 검색·조회됨 |
| (이슈 초안) | | 폐기한 토큰으로 `/mcp`에 접속하면 401로 거부됨 |
| (이슈 초안) | | `read_write` 토큰으로 `create_document`를 호출하면 토큰 주인 소유로 문서가 만들어지고 웹 목록에 나타남 |
| 관리 · 감사 로그 | (ADR-055) | API 토큰으로 REST나 원격 MCP를 통해 만든 문서도 토큰 주인의 이름으로 남음 |

stdio 5항목(로컬 도구 4개 노출·`search_documents` 응답 필드·`list_documents`·`get_document`·`create_document`가 `MCP_USER_ID` 소유·`MCP_USER_ID`를 바꾸면 열람 범위가 바뀜)은 회귀 없이 그대로 통과해야 한다.

## 읽어야 할 파일

- `/docs/ADR.md` — ADR-041(프론트를 같은 오리진에서 — catch-all 라우트), ADR-055, ADR-056
- `backend/openarchive/main.py` — lifespan(풀·`app.state.provider` 예열), 미들웨어, `mount_frontend(app)`이 **맨 마지막**인 이유
- `backend/openarchive/frontend.py` — catch-all이 무엇을 삼키는지
- `backend/openarchive/mcp_server/http.py` — step 1의 `remote_mcp`(`asgi`, `running(provider)`)
- `backend/openarchive/mcp_server/server.py` — step 0
- `backend/tests/conftest.py` — `db_client`(lifespan을 테스트마다 다시 태움), `migrated_db`, `process_all_embedding_jobs`
- `backend/tests/test_mcp_server.py` — `rest_client` fixture(같은 루프에서 `app.router.lifespan_context(app)` + `httpx.ASGITransport`)
- `backend/tests/test_share_access.py` — `test_only_the_allow_list_accepts_a_share_principal`·`test_only_auth_me_reads_the_principal_without_a_guard`(API 경로 전수 검사 — `/mcp`를 붙여도 깨지면 안 된다)
- `backend/.venv/lib/python3.13/site-packages/mcp/client/streamable_http.py` — `streamablehttp_client(url, headers=..., httpx_client_factory=...)`

## 작업

### 1) 테스트 먼저 — 새 파일 `backend/tests/test_mcp_remote.py`

**모든 시험항목 테스트는 SDK 클라이언트(`mcp.client.streamable_http.streamablehttp_client` + `mcp.ClientSession`)로 붙는다** — JSON-RPC를 손으로 만들지 마라. 완료 기준이 "실제 MCP 클라이언트로 원격 접속"이다. 클라이언트를 앱에 붙이는 방법은 재량이다: `httpx_client_factory`로 `httpx.ASGITransport(app=app)`를 주입(앱 lifespan은 `app.router.lifespan_context(app)`로 직접 태움)하거나, 임의 포트에 uvicorn을 띄운다. URL은 경로가 정확히 `/mcp`여야 한다(끝 슬래시 없이).

시드: 사용자 `kim`·`lee`를 만들고(`services.auth.create_user`), lee의 「제한」(private, 부여 없음) 문서와 공개 문서, kim의 문서를 넣은 뒤 `process_all_embedding_jobs(conn, FakeProvider())`로 임베딩한다. 토큰은 `services.auth.create_token`(scope `read`/`read_write`), 공유는 `services.shares.create_share`·`add_document`·`issue_share_token`.

1. **접속·도구 노출**: kim의 `read` 토큰을 `Authorization: Bearer`로만 주고(`DATABASE_URL` 등 DB 정보는 클라이언트에 주지 않는다) `initialize` → `list_tools()`가 4개 이름 → `call_tool("search_documents", {"query": ...})`가 `isError` 거짓이고 결과 항목이 1개 이상.
2. **401**: 토큰 없이 `POST /mcp` → 401. 폐기한 토큰 → 401. (SDK 클라이언트는 401에서 예외를 낸다 — 이 둘은 `httpx`로 직접 `POST /mcp` 상태 코드를 단언해도 된다.)
3. **경로**: `POST /mcp`가 307/308 리다이렉트 없이 처리된다(유효 토큰이면 200, 없으면 401). `GET /mcp`가 프론트 catch-all의 HTML로 응답하지 않는다.
4. **열람 범위**: kim의 토큰으로 lee의 「제한」 문서 제목을 검색하면 그 문서가 결과에 없고, `get_document(<lee 문서 id>)`는 `isError` 참, `list_documents`에도 없다. 같은 검색을 lee의 토큰으로 하면 나온다(판별력 확인). 테스트 동안 `MCP_USER_ID=lee`를 설정해 두어 원격이 환경변수로 떨어지면 실패하게 만든다.
5. **read 토큰 생성 거부**: kim의 `read` 토큰으로 `call_tool("create_document", ...)` → `isError` 참, 응답 텍스트에 "쓰기 권한이 필요합니다", `documents`에 그 제목 행 0개.
6. **read_write 생성**: kim의 `read_write` 토큰으로 생성 → `isError` 거짓, `owner_id='kim'`, 같은 토큰으로 REST `GET /api/documents`에 그 문서가 나타남, `audit_log`에 `document_created`·`actor='kim'`·`actor_via='mcp'` 1행.
7. **공유 토큰**: 공유에 lee의 공개 문서 하나만 넣고 공유 토큰으로 접속 → `search_documents`·`list_documents` 결과가 그 문서뿐, 넣지 않은 공개 문서의 `get_document`는 `isError` 참, `create_document`는 `isError` 참이고 문서가 생기지 않음.
8. **stdio 무관**: 이 파일의 테스트는 `MCP_USER_ID`가 비어 있어도 전부 통과한다(원격은 환경변수를 읽지 않는다).

기존 `test_main.py`(lifespan)·`test_share_access.py`(경로 전수)·`test_mcp_server.py`·`test_mcp_http.py`가 그대로 통과해야 한다. `/mcp`가 `APIRoute`가 아니라서 공유 허용 목록 검사에 섞이지 않는지, 섞인다면 그 이유를 확인하고 목록을 바꾸지 말고 붙이는 방식을 고친다.

### 2) 구현 — `backend/openarchive/main.py`

- lifespan: `app.state.provider`를 예열한 뒤 `async with remote_mcp.running(app.state.provider):` 안에서 `yield`한다. 풀 닫기 순서는 지금 `finally`를 유지한다(원격 매니저가 먼저 끝나고 풀이 닫힌다).
- `/mcp`를 붙인다. **`mount_frontend(app)`보다 먼저.** `POST /mcp`(끝 슬래시 없음)가 리다이렉트 없이 `remote_mcp.asgi`에 닿아야 한다 — Starlette `Mount("/mcp", ...)`는 `/mcp`를 `/mcp/`로 리다이렉트하고, 하위 앱이 `/mcp` 경로를 또 가지면 `/mcp/mcp`가 된다. 방법은 재량(예: `app.router.routes`에 경로 `/mcp`의 Starlette `Route(endpoint=ASGI 앱)` 추가, 또는 하위 앱 경로 조정)이되 테스트 3이 판정한다.
- `RetryOnUnavailable`은 그대로 앱 전체를 감싼다. `POST /mcp`는 그 재시도 목록에 넣지 않는다(도구 쪽 `with_backoff`가 재시도한다 — 이중 재시도 금지).

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_mcp_remote.py tests/test_mcp_http.py tests/test_mcp_server.py tests/test_main.py tests/test_share_access.py tests/test_architecture.py tests/test_retry.py -q
cd backend && .venv/bin/ruff check .
```



## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 수동 관통 1회(로그를 summary에 한 줄로): 로컬 컨테이너 DB로 `EMBEDDING_PROVIDER=fake uvicorn openarchive.main:app --port 8765`를 띄우고, 테스트와 같은 방식으로 발급한 토큰으로 `curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8765/mcp`가 401, `-H "Authorization: Bearer <토큰>" -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'`가 200인지 확인하고 서버를 끈다.
3. mutant 확인: `mount_frontend(app)` 뒤로 `/mcp` 등록을 옮기면 테스트 3이 실패해야 한다. 확인 뒤 되돌린다.
4. 아키텍처 체크리스트: CLAUDE.md CRITICAL(DSN은 `DATABASE_URL`로만, 주체는 토큰에서만, 감사는 DB가 쓴다)을 지키는가.
5. `phases/m28-remote-mcp/index.json`의 step 2를 갱신한다.

## 금지사항

- 원격 MCP를 별도 포트·프로세스로 띄우지 마라. 이유: D1 — 명세서 계약이 `:8000/mcp`이고 프로바이더를 공유해야 한다.
- `/mcp`를 `RetryOnUnavailable`의 재시도 대상에 넣지 마라. 이유: 도구가 이미 `with_backoff`로 재시도한다. 요청 전체를 다시 태우면 키 없는 쓰기가 중복될 수 있다(ADR-047).
- 시험항목 테스트에서 MCP SDK 클라이언트를 손으로 만든 JSON-RPC로 대체하지 마라. 이유: 완료 기준이 실제 클라이언트 접속이다.
- 테스트에서 토큰 검증·DB를 Mock하지 마라. 이유: CLAUDE.md — DB 의존 테스트는 실제 컨테이너로.
- `test_share_access.py`의 허용 목록 상수를 바꾸지 마라. 이유: 공유 허용 목록은 REST 경로의 계약이다 — 원격 MCP의 공유 범위는 도구 안의 열람 술어가 지킨다.
- 기존 테스트를 깨뜨리지 마라
