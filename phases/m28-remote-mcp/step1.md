# Step 1: mcp-http

원격 MCP의 ASGI 진입점 `backend/openarchive/mcp_server/http.py`를 만든다 — Bearer 토큰 미들웨어 + 원격 `FastMCP` 인스턴스(stateless·JSON 응답·DNS rebinding 보호 해제) + lifespan마다 새로 만드는 세션 매니저. `main.py`에 붙이는 것은 step 2다.

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

- `/docs/ADR.md` — ADR-034(위임 토큰), ADR-044 「공유」, ADR-048, ADR-056
- `backend/openarchive/mcp_server/server.py` — step 0이 만든 `McpPrincipal`·`build_server`·`principal_from_token`·`WriteNotAllowed`
- `backend/openarchive/services/auth.py` — `validate_token`, `AuthenticationFailed`
- `backend/openarchive/api/deps.py` — REST가 Bearer를 해석하는 방식(`BEARER_PREFIX`, 대소문자 무시 스킴 비교)
- `backend/openarchive/api/retry.py` — `RetryOnUnavailable`(API 앱 전체를 감싸는 미들웨어, 분류된 DB 오류를 503으로 바꾼다)
- `backend/openarchive/db.py` — `connection()`
- `backend/.venv/lib/python3.13/site-packages/mcp/server/fastmcp/server.py` — `FastMCP.__init__`의 `transport_security` 자동 활성화(host가 localhost면 켜짐), `streamable_http_app()`, `StreamableHTTPASGIApp`
- `backend/.venv/lib/python3.13/site-packages/mcp/server/streamable_http_manager.py` — `StreamableHTTPSessionManager.run()`은 **인스턴스당 한 번만** 부를 수 있다(`_has_started`)
- `backend/.venv/lib/python3.13/site-packages/mcp/server/lowlevel/server.py` — `Server.run`이 lifespan에 매번 들어간다
- `backend/tests/test_mcp_server.py` — 토큰·공유·문서 시드 헬퍼 선례

## 작업

### 1) 테스트 먼저 — 새 파일 `backend/tests/test_mcp_http.py`

`remote_mcp.asgi`를 테스트용 최소 ASGI 앱(예: lifespan에서 `remote_mcp.running(FakeProvider())`에 들어가는 Starlette)에 붙여 `httpx.ASGITransport`로 부른다. 실제 DB(`migrated_db` fixture)와 풀을 쓴다 — 토큰 대조를 Mock하지 마라.

1. Authorization 헤더 없음 → 401, 응답에 `WWW-Authenticate` 헤더가 있고 `Bearer`로 시작한다.
2. `Authorization: Bearer <틀린 값>` → 401. `Authorization: Basic ...` → 401. `Authorization: Bearer ` (빈 토큰) → 401.
3. 발급 뒤 `services.auth.revoke_token`으로 폐기한 토큰 → 401.
4. 유효한 사용자 토큰으로 MCP `initialize` → `tools/list`를 보내면 도구 4개(`search_documents`·`get_document`·`list_documents`·`create_document`)가 나온다. JSON-RPC 요청을 손으로 만들어도 되고 SDK 클라이언트를 써도 된다. Streamable HTTP 요청은 `Accept: application/json, text/event-stream`과 `Content-Type: application/json`을 요구한다.
5. **Host 헤더가 localhost가 아니어도**(예: `Host: 192.168.0.10:8000`, `Origin: http://192.168.0.10:8000`) 유효한 토큰이면 4가 성공한다 — DNS rebinding 보호가 꺼져 있다는 증거(D5).
6. 유효한 토큰의 `tools/call search_documents`가 토큰 주인의 열람 범위로 동작한다 — 다른 사용자의 private 문서가 나오지 않는다. 같은 테스트에서 `MCP_USER_ID`를 그 private 문서의 소유자로 설정(`monkeypatch.setenv` + `get_settings.cache_clear()`)해 두어, 원격이 `MCP_USER_ID`로 떨어지면 실패하게 만든다.
7. `running(...)`을 한 프로세스에서 **두 번 연속** 들어갔다 나와도(같은 `remote_mcp` 객체) 두 번째에서도 4가 성공한다. 이유: `tests/conftest.py`의 `db_client`가 같은 `app`의 lifespan을 테스트마다 다시 돌리는데, SDK 세션 매니저는 인스턴스당 `run()`이 한 번뿐이다.
8. `running(...)` 밖에서 들어온 요청은 매달리지 않고 즉시 5xx로 끝난다(재량 — 503 권장).
9. 원격 도구 호출이 `running(provider)`에 넘긴 프로바이더 객체를 쓴다 — 모듈 전역 `server.provider`가 아니다(예: 호출 수를 세는 프로바이더 래퍼로 확인).

### 2) 구현 — `backend/openarchive/mcp_server/http.py`

```python
class RemoteMcp:
    """API 앱에 붙는 원격 MCP. 주체는 Bearer 토큰에서만 정한다 (ADR-056)."""

    asgi: ASGIApp                     # 인증 미들웨어로 감싼 Streamable HTTP 진입점
    def running(self, provider: EmbeddingProvider) -> AbstractAsyncContextManager[None]: ...
        # API lifespan에서 들어간다. 들어갈 때마다 새 StreamableHTTPSessionManager를 만들어 run()한다.

remote_mcp: RemoteMcp                 # 모듈 전역 인스턴스 — main.py가 import한다
```

- 원격 `FastMCP`는 step 0의 `build_server(resolve_principal, resolve_provider, stateless_http=True, json_response=True, transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))`로 만든다. **lifespan을 넘기지 않는다**(D4 — 요청마다 풀이 닫힌다).
- 세션 매니저는 `FastMCP.streamable_http_app()`의 지연 생성에 기대지 말고 `running()`마다 `StreamableHTTPSessionManager(app=<원격 FastMCP의 저수준 서버>, json_response=True, stateless=True, security_settings=<위와 같은 설정>)`을 새로 만든다. `asgi`는 그때그때의 현재 매니저의 `handle_request`로 넘긴다. SDK 비공개 속성(`_mcp_server`)에 기대야 하면 한 곳에서만 쓰고 이유를 주석으로 남긴다.
- 인증 미들웨어(순수 ASGI — `BaseHTTPMiddleware`를 쓰지 마라, `retry.py` 머리말 참조): `http` scope만 본다. Bearer 스킴을 대소문자 무시로 해석하고, `async with connection() as conn: user = await validate_token(conn, token)`. `AuthenticationFailed`·헤더 없음·다른 스킴이면 401 JSON(`{"detail": "API 토큰이 필요합니다."}` 수준의 문구) + `WWW-Authenticate: Bearer`. 성공하면 `principal_from_token(user)`를 원격 도구의 `resolve_principal`이 읽을 수 있게 넘긴다(contextvar 또는 요청 scope — 재량). 원격 `resolve_principal`은 주체가 없으면 예외를 던진다 — **절대 `MCP_USER_ID`나 익명으로 대체하지 않는다.**
- contextvar를 쓰면 stateless 매니저가 요청을 자기 task group에서 실행한다는 점을 확인하라 — 테스트 6이 실제 HTTP 왕복으로 주체 전달을 증명해야 한다.
- DB 일시 불가용(`psycopg.Error`) 예외는 미들웨어에서 삼키지 말고 올린다 — API 앱의 `RetryOnUnavailable`이 503으로 바꾼다(step 2에서 붙는다).
- 인증 실패를 감사 로그에 남기지 않는다(감사 대상 아님, ADR-055).

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_mcp_http.py tests/test_mcp_server.py tests/test_architecture.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인 — 각각에서 테스트가 실패해야 한다. 확인 뒤 되돌린다.
   - `TransportSecuritySettings(...)`를 넘기지 않는다 → 테스트 5 실패
   - `running()`이 매번 새 매니저를 만들지 않고 하나를 재사용한다 → 테스트 7 실패
   - 원격 `resolve_principal`이 주체가 없을 때 stdio 주체(`MCP_USER_ID`)를 돌려준다 → 테스트 6 실패(인증 우회 경로가 생기면 401 테스트도 다시 확인)
3. 아키텍처 체크리스트: `mcp_server` 아래에 `.execute(`가 없는가(`test_architecture.py`), SDK `AuthSettings`/`token_verifier`를 쓰지 않았는가(D3), CORS 미들웨어를 더하지 않았는가(D5).
4. `phases/m28-remote-mcp/index.json`의 step 1을 갱신한다(summary에 `remote_mcp.asgi`·`remote_mcp.running(provider)` 사용법과 주체 전달 방식을 적는다).

## 금지사항

- SDK의 `AuthSettings`·`token_verifier`·OAuth 메타데이터 라우트를 쓰지 마라. 이유: D3 — 없는 OAuth 인가 서버를 광고한다(ADR-056 기각 대안).
- CORS 미들웨어를 더하지 마라. 이유: D5의 안전 근거가 "브라우저가 교차 오리진으로 `Authorization`을 싣지 못한다"이다.
- 원격 FastMCP에 `server.py`의 풀 lifespan을 주지 마라. 이유: stateless에서는 요청마다 lifespan이 돌아 `close_pool()`이 불린다.
- 토큰 해석을 새로 구현하지 마라(해시 계산·SQL). 이유: D3 — REST와 같은 `validate_token` 하나여야 폐기·scope·공유 규칙이 갈라지지 않는다.
- `main.py`를 건드리지 마라. 이유: step 2의 범위다.
- 기존 테스트를 깨뜨리지 마라
