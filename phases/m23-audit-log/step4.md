# Step 4: actor-mcp

stdio MCP 서버의 쓰기 도구(`create_document`)가 행위자를 `MCP_USER_ID`로 DB에 넘기게 한다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- 감사 로그는 DB 트리거가 쓰고(step 0·1), 행위자는 트랜잭션 범위 GUC로 넘긴다. 넘기는 수단은 `backend/openarchive/services/audit.py`의 `set_actor(conn, *, actor, via, share_id=None)` **하나뿐**이다(step 3). 이 헬퍼는 `set_config(..., true)`만 쓰고, autocommit 연결에서 트랜잭션 밖이면 `RuntimeError`를 던진다. 앱 코드가 GUC를 직접 쓰거나 `audit_log`에 INSERT하면 `test_architecture.py`가 실패한다.
- stdio MCP의 주체는 서버 환경 `MCP_USER_ID`다(ADR-036·057 — 원격 MCP는 #188에서 Bearer 토큰 주인이 주체가 되며 이 step의 범위가 아니다). `mcp_server/server.py`의 도구는 호출마다 `db.connection()`으로 풀 연결 하나를 빌리고, 이 연결은 autocommit이 아니어서 도구 호출 하나가 암묵 트랜잭션 하나다.
- 읽기 도구(`search_documents`·`get_document`·`list_documents`)는 기록 대상이 아니다(ADR-055 결정 6) — 행위자를 걸 필요가 없다.

## 읽어야 할 파일

- `backend/openarchive/services/audit.py` — step 3 산출물
- `backend/openarchive/mcp_server/server.py` — `create_document` 도구(216행 근처)와 `MissingUserContext`
- `backend/tests/test_mcp_server.py` — MCP 도구 테스트 헬퍼
- `backend/tests/test_audit.py` — step 3의 감사 행 조회 헬퍼
- `/docs/ADR.md` — ADR-036(MCP 쓰기), ADR-055

## 작업

### 1) 테스트 먼저 — `backend/tests/test_mcp_server.py`에 추가

1. `MCP_USER_ID=alice`로 `create_document` 도구를 부르면 감사 행 `document_created`, `actor='alice'`, `actor_via='mcp'`, 만든 문서 id·제목 일치.
2. `MCP_USER_ID`가 없거나 빈 문자열이면 기존대로 `MissingUserContext`이고 감사 행이 생기지 않는다.

### 2) 구현 — `mcp_server/server.py`

- `create_document`의 `async with connection() as conn:` 안, 문서를 만들기 전에 `await set_actor(conn, actor=user_id, via="mcp")`.
- 다른 도구는 고치지 않는다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_mcp_server.py tests/test_architecture.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: `set_actor` 호출을 지우면 테스트 1이 실패해야 한다(`actor IS NULL`).
3. `phases/m23-audit-log/index.json`의 step 4를 갱신한다.

## 금지사항

- `SET openarchive.…`·`set_config(...)`를 직접 쓰지 마라 — `set_actor`만 쓴다. 이유: 세션 범위 SET은 HA 실측에서 다른 클라이언트로 75/100 샜다.
- 원격 MCP(Streamable HTTP)나 토큰 인증을 만들지 마라. 이유: #188의 범위다.
- 읽기 도구에 기록을 붙이지 마라. 이유: ADR-055 결정 6.
- 기존 테스트를 깨뜨리지 마라
