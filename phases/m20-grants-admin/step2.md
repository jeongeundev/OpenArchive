# Step 2: document-access

문서의 열람 범위(조직 공개/제한 + 부여 대상)를 **조회·통째로 교체**하는 서비스와, 문서 생성 시 부여
대상을 같은 트랜잭션에서 넣는 경로를 `services/documents.py`에 둔다. 라우터·MCP는 다음 step들이다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 「구현 형태」·「관리 경로 (2026-10-01, #97 b)」, ADR-047(멱등키), ADR-027
- `backend/openarchive/services/grants.py` — step 1 산출물(`resolve_grantees`·`insert_grants`·`UnknownGrantee`)
- `backend/openarchive/services/documents.py` — `ensure_visible`, `_load_for_write`(a에서 열람 술어로 존재를 판정하도록 고쳤다 — 보이면 403, 안 보이면 404), `create_document`, `create_text_document`, `_create_once`, `_request_hash`, `_insert_document`, `update_tags`(소유자 쓰기의 선례), 예외 클래스들
- `backend/openarchive/worker.py` 363행 근처 — 문서 행을 `FOR NO KEY UPDATE`로 잠그는 이유(024와 같은 잠금 수준)
- `backend/tests/test_documents.py`, `backend/tests/test_visibility.py`

## 작업

### 1) 테스트 먼저 — `backend/tests/test_documents.py`(또는 새 `test_document_access.py`)

1. `get_access(conn, id, user_id=소유자)` → `{"visibility", "users": [사용자명…], "groups": [그룹명…]}`, 이름순.
2. 볼 수 있는 비소유자(public 문서의 다른 사용자, 부여받은 사용자) → `DocumentAccessDenied`. 볼 수 없는 사용자·익명(`None`) → `DocumentNotFound`. `set_access`도 같다.
3. `set_access(..., visibility="private", users=["bob"], groups=["인사팀"])` → 기존 부여를 **모두 지우고** 정확히 이 대상만 남는다. 그 뒤 bob은 문서를 보고, 이전에 부여됐던 carol은 못 본다.
4. `set_access(..., visibility="public", users=[], groups=[])` → visibility가 public이 되고 부여 행이 0이다.
5. `visibility="public"`에 대상이 하나라도 있으면 `GrantsOnPublicDocument`, DB 변화 없음.
6. 모르는 이름 → `UnknownGrantee`, **DB 변화 없음**(visibility도 그대로 — 교체는 원자적이다).
7. `create_text_document(..., visibility="private", grant_users=["bob"], grant_groups=["인사팀"])` → 문서와 부여가 함께 생긴다. bob·인사팀 구성원이 보고 다른 사용자는 못 본다. `create_document`(업로드)도 같다.
8. 생성 시 public + 대상 → `GrantsOnPublicDocument`, 모르는 이름 → `UnknownGrantee`, 둘 다 **문서가 생기지 않는다**(documents 행 수 그대로).
9. 멱등키: 같은 키 + 같은 대상(순서·중복만 다름) → 처음 문서를 돌려준다. 같은 키 + 다른 대상 → `IdempotencyKeyReused`.
10. 기존 호출(인자 없이) → 지금처럼 부여 0행.

### 2) 구현

```python
class GrantsOnPublicDocument(Exception): ...
# 메시지: "조직 공개 문서에는 부여 대상이 필요 없습니다. 대상에게만 열려면 visibility=private로 보내세요."

async def get_access(conn, document_id: UUID, *, user_id: str | None) -> dict
async def set_access(conn, document_id: UUID, *, user_id: str | None,
                     visibility: str, users: list[str], groups: list[str]) -> dict  # 교체 후 get_access와 같은 모양

# 기존 시그니처에 키워드 인자 추가 (기본 None = 부여 없음)
async def create_document(..., grant_users: list[str] | None = None, grant_groups: list[str] | None = None) -> dict
async def create_text_document(..., grant_users: list[str] | None = None, grant_groups: list[str] | None = None) -> dict
```

핵심 규칙:
- **권한 판정은 `_load_for_write`를 쓴다**(또는 같은 술어 SQL). 열람 규칙을 Python으로 다시 쓰지 마라 — a가 고친 결함(부여 대상의 403이 404가 되던 것)이 재발한다. `get_access`도 소유자 전용이므로 같은 판정이다.
- `set_access`는 한 트랜잭션: 문서 행을 `SELECT … FOR NO KEY UPDATE`로 잠그고(동시 교체가 DELETE/INSERT 사이에 끼면 유니크 충돌이 난다) → 이름 해석 → `UPDATE documents SET visibility` → 그 문서의 `document_grants` DELETE → `insert_grants`. 이름 해석은 **변경 전에** 해서 실패 시 아무것도 바뀌지 않게 한다.
- `visibility` 값 검증은 기존 `InvalidVisibility`를 재사용한다.
- 생성 경로: public+대상 검사와 이름 해석을 `insert()` 클로저 안 또는 그 전에 하되, 부여 INSERT는 **문서 INSERT와 같은 트랜잭션**(`_create_once`가 여는 것)에서 한다. 문서만 커밋되고 부여가 빠진 상태가 생기면 안 된다.
- `_request_hash`에 부여 대상을 넣는다 — 정렬·중복 제거한 목록으로(같은 요청의 순서 차이가 다른 요청이 되지 않게).
- 응답 요약(`SUMMARY_COLUMNS`)은 바꾸지 않는다. 부여 목록은 `get_access`로만 나간다 — 목록·검색 응답에 실으면 비소유자에게 부여 대상이 샌다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_documents.py tests/test_visibility.py tests/test_grants.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: 부여 판정이 `VISIBLE_TO_USER` 하나에 기대는가? `embedding_jobs`·`document_edges`에 INSERT하지 않는가(CLAUDE.md CRITICAL)? visibility 변경이 트리거(`UPDATE OF content_hash`)를 건드리지 않는가?
3. `phases/m20-grants-admin/index.json`의 step 2를 갱신한다. summary에 함수·예외·추가 인자 이름을 적는다.

## 금지사항

- 라우터·MCP를 고치지 마라. 이유: step 4·5의 범위다.
- `DocumentSummary`·`SUMMARY_COLUMNS`에 부여 목록을 넣지 마라. 이유: 비소유자에게 부여 대상(누가 이 문서를 보는가)이 샌다.
- public 문서의 부여를 "저장만 하고 무시"하지 마라. 이유: 효력 없는 숨은 상태를 남기지 않기로 했다(ADR-044 관리 경로 결정 4).
- 대상이 있으면 visibility를 자동으로 private로 바꾸지 마라. 이유: 같은 결정 — 암묵 규칙 대신 명시적 400이다.
- 마이그레이션을 추가하지 마라.
- 기존 테스트를 깨뜨리지 마라
