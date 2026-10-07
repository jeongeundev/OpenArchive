# Step 0: mcp-principal

`mcp_server/server.py`의 도구 4개가 **주입받은 주체**로 동작하도록 구조를 바꾼다. 이 step이 끝나도 stdio의 외부 동작은 한 글자도 바뀌지 않는다 — 원격 인스턴스(step 1)가 같은 도구 본체를 다른 주체로 재사용할 수 있게 하는 준비다.

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

- `/docs/ADR.md` — ADR-025, ADR-036, ADR-047, ADR-048, ADR-056
- `/docs/ARCHITECTURE.md` — MCP 서버 절(“MCP 서버는 `openarchive.services`를 직접 재사용한다”), 감사 행위자 표(“stdio MCP `create_document` | `MCP_USER_ID` | `mcp`”)
- `backend/openarchive/mcp_server/server.py` — 지금 구조 전부
- `backend/openarchive/services/auth.py` — `validate_token`이 돌려주는 dict(`kind`·`principal`·`username`·`scope`·`share_id`), `SCOPE_READ_WRITE`·`PRINCIPAL_SHARE`
- `backend/openarchive/api/deps.py` — `require_write_user_id`의 거부 문구("쓰기 권한이 필요합니다.")
- `backend/tests/test_mcp_server.py` — 기존 stdio 테스트 26개. 모듈 전역 함수(`search_documents` 등)와 `mcp` 인스턴스를 직접 부른다
- `backend/tests/test_architecture.py` — `test_mcp_server_does_not_execute_sql_directly`(mcp_server 아래 `.py`에 `.execute(` 금지)

## 작업

### 1) 테스트 먼저 — `backend/tests/test_mcp_server.py`에 추가

1. **주체 주입**: 도구 본체를 사용자 주체 `kim`으로 부르면 kim이 볼 수 있는 문서만 검색·목록·조회되고, 같은 프로세스의 `MCP_USER_ID`가 다른 값(예: `lee`)이어도 결과가 바뀌지 않는다. 즉 본체는 `get_settings().mcp_user_id`를 읽지 않는다.
2. **공유 주체**: 주체가 공유(`share:<uuid>`)면 그 공유에 넣은 문서만 검색·목록·`get_document`로 보이고, 넣지 않은 공개 문서의 `get_document`는 `DocumentNotFound`(stdio와 같은 "없음")다.
3. **쓰기 scope**: `read` 사용자 주체와 공유 주체의 `create_document`는 쓰기 권한 예외("쓰기 권한이 필요합니다.")로 거부되고 `documents` 행 수가 늘지 않는다. **DB 연결을 빌리기 전에** 거부해야 한다(풀을 열지 않은 상태에서도 같은 예외).
4. **소유자·감사**: `read_write` 사용자 주체 `kim`의 `create_document`는 `owner_id='kim'`으로 저장되고 `audit_log`에 `action='document_created'`, `actor='kim'`, `actor_via='mcp'` 행이 하나 남는다.
5. **stdio 인스턴스 불변**: `mcp`(stdio 인스턴스)의 `list_tools()` 이름·입력 스키마가 이 step 전과 같다 — 4개, 이름 `search_documents`·`get_document`·`list_documents`·`create_document`, 각 도구의 `inputSchema`의 `properties` 키 집합이 지금 값과 같고 주체를 나타내는 인자(`user_id`·`principal`·`owner` 등)가 없다. 지금 값은 수정 전에 한 번 찍어 테스트에 상수로 박는다.
6. 기존 26개 테스트는 수정 없이 통과해야 한다(MissingUserContext 문구·`MCP_USER_ID` 미설정 시 public 읽기 허용 포함).

### 2) 구현 — `backend/openarchive/mcp_server/server.py`

시그니처 수준 지시다. 내부 구조는 재량이되 아래 계약을 지킨다.

```python
@dataclass(frozen=True)
class McpPrincipal:
    principal: str | None   # 열람 술어에 넘기는 값 — 사용자명, share:<uuid>, stdio 미설정이면 None
    owner: str | None       # 문서를 만들 때 소유자 — 사용자명. 공유 주체·stdio 미설정은 None
    can_write: bool         # create_document 허용 여부
    share_id: UUID | None = None

class WriteNotAllowed(Exception):
    """read 토큰·공유 토큰의 쓰기 시도. 메시지 "쓰기 권한이 필요합니다."."""

def principal_from_token(user: dict) -> McpPrincipal: ...
    # services.auth.validate_token의 반환값 → 주체. 사용자: principal=owner=username,
    # can_write = scope == SCOPE_READ_WRITE. 공유: principal=user["principal"], owner=None, can_write=False.

def build_server(
    resolve_principal: Callable[[], McpPrincipal],
    resolve_provider: Callable[[], EmbeddingProvider],
    *,
    lifespan=None,
    **fastmcp_settings,
) -> FastMCP: ...
    # 같은 도구 4개(이름·docstring·인자 동일)를 등록한 FastMCP를 만든다.
```

- 도구 본체는 호출 시점에 `resolve_principal()`·`resolve_provider()`를 부른다. stdio 인스턴스: `resolve_principal`은 `MCP_USER_ID`를 읽어 `McpPrincipal(principal=v, owner=v, can_write=True)`(미설정·빈 값이면 `principal=None` — 지금처럼 public 읽기는 되고 `create_document`는 기존 `MissingUserContext` 문구로 거부), `resolve_provider`는 지금의 모듈 전역 `provider`.
- `create_document`의 판정 순서: stdio 미설정(`owner` 없음 + stdio)이면 기존 `MissingUserContext`, `can_write`가 거짓이면 `WriteNotAllowed` — 둘 다 `connection()` 전에. 통과하면 지금처럼 백오프 바깥에서 멱등키를 하나 만들고, 트랜잭션 안에서 `set_actor(conn, actor=owner, via="mcp")` 뒤 `create_text_document(owner_id=owner, ...)`. stdio와 원격 둘 다 이 한 경로를 탄다. "stdio 미설정"과 "공유 주체"를 구분하는 방법은 재량(예: 필드 하나 더)이되, 공유 주체에 `MissingUserContext` 문구("MCP_USER_ID 환경변수를 설정")가 나가면 안 된다 — 원격 사용자에게 의미 없는 안내다.
- 모듈 전역 이름 `mcp`(stdio 인스턴스, 지금의 풀 lifespan 유지)·`main()`·`provider`·`with_backoff`·`DatabaseUnavailable`·`MissingUserContext`와 모듈 전역 함수 `search_documents`·`get_document`·`list_documents`·`create_document`(stdio 주체로 동작)는 남긴다 — 기존 테스트와 stdio 진입점(`python -m openarchive.mcp_server.server`)이 쓴다.
- `create_document` docstring의 "소유자는 서버의 MCP_USER_ID 환경이 정하며"를 transport 중립 문구로 바꾼다: 소유자는 접속 주체(로컬은 `MCP_USER_ID`, 원격은 Bearer 토큰 주인)이며 인자로 지정할 수 없고, 읽기 전용 토큰·공유 토큰으로는 만들 수 없다. 원격 클라이언트도 이 docstring을 도구 설명으로 본다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_mcp_server.py tests/test_architecture.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인 — 아래 각각에서 새 테스트 중 하나 이상이 실패해야 한다. 확인 뒤 되돌린다.
   - 도구 본체가 주입 주체 대신 `get_settings().mcp_user_id`를 읽게 바꾼다 → 테스트 1 실패
   - `can_write` 검사를 지운다 → 테스트 3 실패
   - `set_actor` 호출을 지운다 → 테스트 4 실패
3. 아키텍처 체크리스트: `mcp_server`에서 SQL을 직접 실행하지 않는가, CLAUDE.md CRITICAL(주체를 툴 인자로 받지 않음, 앱이 `audit_log`에 INSERT하지 않음)을 지키는가.
4. `phases/m28-remote-mcp/index.json`의 step 0을 갱신한다(성공 → completed + summary에 새 공개 이름 `McpPrincipal`·`build_server`·`principal_from_token`·`WriteNotAllowed`와 stdio 인스턴스 이름을 적는다).

## 금지사항

- 도구 인자에 주체(사용자명·토큰·share id)를 추가하지 마라. 이유: ADR-025·036·056 결정 3 — 주체를 요청 데이터로 받지 않는다.
- 이 step에서 HTTP transport·`main.py`를 건드리지 마라. 이유: step 1·2의 범위다(한 step 한 모듈).
- 도구의 응답 모양·인자·이름을 바꾸지 마라. 이유: stdio 명세서 5항목과 `test_mcp_server.py`의 REST 응답 비교가 계약이다.
- `mcp_server` 안에서 `conn.execute(...)`로 SQL을 쓰지 마라. 이유: `test_architecture.py`가 금지한다 — 서비스 함수를 부른다.
- 기존 테스트를 깨뜨리지 마라
