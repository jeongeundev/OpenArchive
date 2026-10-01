# Step 5: mcp-grants

MCP `create_document` 도구에 부여 대상 인자를 둔다. 서비스(`create_text_document`의 `grant_users`·
`grant_groups`)는 step 2에 있다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 「관리 경로 (2026-10-01, #97 b)」, ADR-036(MCP 쓰기 도구)
- `backend/openarchive/mcp_server/server.py` — `create_document`(MCP_USER_ID, 멱등키를 백오프 바깥에서 만드는 이유), 예외가 도구 오류로 나가는 방식
- `backend/openarchive/services/documents.py` — `create_text_document`, `GrantsOnPublicDocument`
- `backend/openarchive/services/grants.py` — `UnknownGrantee`
- `backend/tests/test_mcp_server.py`

## 작업

### 1) 테스트 먼저 — `backend/tests/test_mcp_server.py`

1. `create_document(..., visibility="private", grant_users=["bob"], grant_groups=["인사팀"])` → 문서와 부여가 생기고 bob이 볼 수 있다.
2. public + 대상 → 도구 오류(서비스 문구가 담긴다), 모르는 이름 → 도구 오류(이름이 담긴다). 둘 다 문서가 생기지 않는다.
3. 인자를 주지 않으면 지금과 같다(부여 0).
4. 도구 스키마(FastMCP가 노출하는 입력 스키마)에 `grant_users`·`grant_groups`가 있다.

### 2) 구현

```python
async def create_document(
    title: str,
    content: str,
    content_type: Literal["txt", "md"] = "md",
    tags: list[str] | None = None,
    visibility: Literal["public", "private"] = "public",
    grant_users: list[str] | None = None,
    grant_groups: list[str] | None = None,
) -> dict
```

- docstring(에이전트가 읽는 설명)에 적는다: public = 조직 공개, private = 소유자 + 부여 대상. 대상은 사용자명·그룹명이며 `visibility="private"`일 때만 줄 수 있다. 기존 문서의 열람 범위는 이 도구로 바꿀 수 없다(웹 세션 전용).
- 서비스 예외를 삼키거나 다른 문구로 바꾸지 마라 — 기존 도구들이 오류를 내보내는 방식을 따른다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_mcp_server.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: MCP 서버가 서비스만 재사용하는가(CLAUDE.md — 비즈니스 로직은 services)? 열람 범위 변경 도구를 추가하지 않았는가?
3. `phases/m20-grants-admin/index.json`의 step 5를 갱신한다.

## 금지사항

- 열람 범위 변경 MCP 도구(`set_access` 등)를 만들지 마라. 이유: 기존 문서의 범위 변경은 세션 전용이다(ADR-044 관리 경로 결정 2). stdio MCP는 세션이 아니다.
- 그룹 관리 MCP 도구를 만들지 마라. 이유: 관리자·세션 전용이다(ADR-034).
- 대상 검증을 MCP 쪽에 중복 구현하지 마라. 이유: 코어가 자기 계약을 지킨다(step 2).
- 기존 테스트를 깨뜨리지 마라
