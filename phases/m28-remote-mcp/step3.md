# Step 3: remote-mcp-docs

원격 MCP 구현을 문서에 반영한다 — ADR-056 「구현 때 정함」을 결정으로 닫고, "결정·미구현(#188)"으로 적힌 곳을 구현 상태로 고치고, 운영 문서에 클라이언트 등록 절차를 더한다. 코드는 바꾸지 않는다.

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

- `backend/openarchive/mcp_server/server.py`, `backend/openarchive/mcp_server/http.py`, `backend/openarchive/main.py` — step 0~2가 실제로 만든 것(문서는 코드를 따른다 — 아래 결정과 코드가 다르면 코드를 확인하고 사실대로 쓴 뒤 summary에 적는다)
- `backend/tests/test_mcp_remote.py`, `backend/tests/test_mcp_http.py` — 각 주장의 근거 테스트
- `/docs/ADR.md` — ADR-056(「구현 때 정함」 3항목), ADR-008·025의 2026-10-05 개정 주석, 문서 끝의 개정 기록 형식 선례(예: ADR-054 「2026-10-06 구현 결정」)
- `/docs/ARCHITECTURE.md` — 디렉토리 트리의 `mcp_server/server.py` 줄, MCP 서버 절, 감사 행위자 표(`stdio MCP create_document | MCP_USER_ID | mcp`), ADR-048 백오프 문단("MCP는 HTTP를 거치지 않고…"), "MCP 서버는 예열하지 않는다" 문단
- `/docs/OPERATIONS.md` — 「MCP 서버」 절
- `/docs/PRD.md` — 13·192·296·425·441·463행 부근의 "결정·미구현 #188"
- `/docs/ROADMAP.md` — 34행 Interface 표
- `/README.md` — MCP 등록 예시(stdio `args: ["-m", "openarchive.mcp_server.server"]`)
- `/CLAUDE.md` — 기술 스택의 "원격은 결정·미구현(#188, ADR-056)"

## 작업

1. **`docs/ADR.md` ADR-056**: 상태 줄에 구현 완료(#188)를 더하고, 끝에 「2026-10-07 구현 결정」 절을 쓴다 — D1 배치(API 앱 `/mcp`, 프로바이더·풀 공유, 별도 프로세스 기각 이유), D2 stateless·JSON 응답, D3 자체 Bearer 미들웨어(SDK `AuthSettings` 기각 이유 — OAuth 메타데이터 광고), D4 transport별 FastMCP 인스턴스와 원격에 풀 lifespan을 주지 않는 이유(SDK가 lifespan을 요청마다 돈다), D5 DNS rebinding 보호 해제의 근거와 대가(Bearer 필수 + CORS 없음, Host 허용 목록을 두지 않음 — 평문 HTTP에서 토큰이 노출되므로 다른 PC에서 쓸 때는 TLS 종단 프록시 뒤에 둘 것), D6 scope·감사(`via='mcp'`, DB에 닿기 전 거부), 「구현 때 정함」 3번째(DB 불가용·멱등키)는 stdio와 같은 `with_backoff`·호출당 멱등키를 그대로 쓴다는 것과 인증 단계의 DB 불가용은 `RetryOnUnavailable`이 503으로 바꾼다는 것. 「구현 때 정함」 목록은 지우지 말고 각 항목 끝에 "→ 2026-10-07 결정(아래)"를 단다.
2. **`docs/ARCHITECTURE.md`**: 트리에 `mcp_server/http.py` 줄 추가(`server.py` 설명을 "FastMCP 도구 4개 — stdio 인스턴스·build_server"로), MCP 서버 절에 원격 경로 문단(주체=토큰, scope, 공유, 같은 서비스·같은 술어, `MCP_USER_ID`는 stdio 전용), 감사 행위자 표에 `원격 MCP create_document | Bearer 토큰 주인 | mcp` 행, 백오프 문단에 원격 도구도 같은 `with_backoff`이고 HTTP 응답으로 503을 받는 것은 인증 단계뿐이라는 점, "MCP 서버는 예열하지 않는다" 문단을 "stdio MCP는 예열하지 않는다. 원격은 API가 예열한 프로바이더를 쓴다"로.
3. **`docs/OPERATIONS.md` 「MCP 서버」**: 「로컬(stdio)」과 「원격(HTTP)」 소절로 나눈다. 원격 소절 — `openarchive serve`면 따로 띄울 것 없음, 설정 화면에서 토큰 발급(`read`면 읽기 3개만, 생성은 `read_write`), 등록 예시 두 개: Claude Code `claude mcp add --transport http openarchive http://<서버>:8000/mcp --header "Authorization: Bearer <토큰>"`와 일반 JSON 설정(`"type": "http"`, `"url"`, `"headers"`). 공유 토큰으로도 등록할 수 있고 공유에 넣은 문서만 보인다는 것, 401이면 토큰 폐기·오타 확인, 다른 PC에서 쓸 때는 TLS 프록시 뒤에 둘 것(토큰이 평문으로 흐른다), Host 허용 목록이 없는 이유 한 줄(ADR-056 링크).
4. **`docs/PRD.md`·`docs/ROADMAP.md`·`README.md`·`CLAUDE.md`**: "결정·미구현(#188)"·"원격 MCP는 결정·미구현" 문구를 구현 상태로 고친다(예: PRD C4 "소비 4면(Web UI·REST·로컬 MCP·원격 MCP)"). PRD 425행처럼 "REST(원격 MCP는 …)만"인 곳은 "REST·원격 MCP로"로. README에는 stdio 예시 옆에 원격 등록 예시를 짧게 더한다. 그 밖에 `grep -rn "#188\|원격 MCP" docs README.md CLAUDE.md`로 남은 미구현 표현을 찾아 고친다 — 단 ADR 본문의 과거 기록(상태 줄 이력·개정 주석의 원래 문장)은 고치지 않는다.
5. 사용자 대상 문구에 "항상 최신"·"실시간 동기화"·"무중단"을 쓰지 않는다(CLAUDE.md).

## Acceptance Criteria

```bash
cd /Users/kje/00_Workspace/01_Coding/project/OpenSQL
! grep -n "결정·미구현(#188\|결정·미구현 #188" docs/PRD.md docs/ROADMAP.md docs/ARCHITECTURE.md docs/OPERATIONS.md README.md CLAUDE.md
grep -n "2026-10-07 구현 결정" docs/ADR.md
grep -n "mcp_server/http.py" docs/ARCHITECTURE.md
grep -n "claude mcp add --transport http" docs/OPERATIONS.md
cd backend && .venv/bin/pytest tests/test_mcp_remote.py tests/test_mcp_http.py tests/test_mcp_server.py -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다(첫 줄은 남은 미구현 표현이 없으면 성공 — `!`가 grep 결과를 뒤집는다. zsh에서 돌리지 말고 bash로).
2. 문서에 적은 주장마다 근거 코드·테스트가 있는지 대조한다(특히 401 조건, `via='mcp'`, Host 검사 해제, 프로바이더 공유).
3. `phases/m28-remote-mcp/index.json`의 step 3을 갱신한다.

## 금지사항

- 코드·테스트를 바꾸지 마라. 이유: 문서 step이다 — 코드와 문서가 어긋나면 문서를 코드에 맞추고 summary에 적는다.
- ADR 본문의 과거 기록(이전 날짜의 결정 문장)을 지우거나 고쳐 쓰지 마라. 이유: ADR은 이력이다 — 개정은 날짜 붙은 새 절로 덧붙인다.
- `notes/` 아래 파일을 고치지 마라. 이유: 제출본·조사 노트는 이 phase 범위 밖이다.
- 기존 테스트를 깨뜨리지 마라
