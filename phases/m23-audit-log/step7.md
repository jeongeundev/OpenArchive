# Step 7: audit-api

관리자 전용 감사 로그 조회 API `GET /api/admin/audit`를 만든다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- `audit_log`(028) 칼럼: `id bigserial, occurred_at timestamptz, action text, actor text|NULL, actor_via text|NULL, db_role text, document_id uuid|NULL, document_title text|NULL, detail jsonb`. `action` 값: `document_created`, `text_updated`, `document_deleted`, `access_changed`, `group_member_changed`, `original_replaced`, `original_downloaded`. `actor_via` 값: `session | token | mcp | cli | share | worker` 또는 NULL. 인덱스: `(occurred_at DESC, id DESC)`, `(actor, id DESC)`, `(action, id DESC)`.
- `detail` 모양(step 1): `text_updated {version}`, `access_changed {kind:"visibility", before, after}` 또는 `{kind:"grant", change:"added"|"removed", grantee_type:"user"|"group", grantee}`, `group_member_changed {change, group, user}`, `original_replaced`·`original_downloaded {file_version}` (+ 공유일 때 `share_id`, `share_name`).
- 명세서 시험항목(관리 > 감사 로그 > 조회):
  - 관리자가 메뉴의 「감사 로그」를 누르면 기록이 **시각·사용자·동작·대상 문서 제목**과 함께 **최신순**으로 표시됨
  - **사용자·동작으로 걸러 보면** 그 조건의 기록만 표시됨
  - 일반 사용자는 메뉴에 「감사 로그」가 없고, 주소를 직접 열면 **"관리자 권한이 필요합니다."**가 표시됨
- 조회는 관리자 전용이고 `/api/admin/*` 아래라 **세션 전용**이다(ADR-034 결정 6, ADR-055 결정 7). 기존 `api/deps.py`의 `require_admin`이 세션 확인 + `is_admin` 확인을 하고 일반 사용자에게 403 "관리자 권한이 필요합니다."를 준다 — 그대로 쓴다.
- **관리자에게 대상 문서 제목을 보이는 것은 ADR-027·040의 명시적 예외**다(ADR-055 결정 8). 제목만이다 — 본문·발췌·태그는 응답에 넣지 않는다. 이 예외는 이 API 하나에 한정된다. 열람 술어(`services/visibility.py`)를 이 조회에 적용하지 않는 이유가 이것이다.
- 감사 행 조회는 그 자체로 기록하지 않는다(읽기는 기록하지 않는다 — ADR-055 결정 6).

## 읽어야 할 파일

- `backend/openarchive/services/audit.py` — step 3의 `set_actor`(이 파일에 조회 함수를 더한다)
- `backend/openarchive/api/admin.py`, `backend/openarchive/api/groups.py` — 관리 라우터 선례(prefix `/api/admin/...`, `require_admin`)
- `backend/openarchive/api/deps.py` — `require_admin`, `Connection`
- `backend/openarchive/api/schemas.py` — 응답 모델 선례
- `backend/openarchive/main.py` — 라우터 등록
- `backend/tests/test_groups_api.py` — 관리자 세션·일반 사용자·토큰 거부 테스트 선례
- `backend/tests/test_audit.py` — 감사 행 만드는 헬퍼
- `/docs/ADR.md` — ADR-055, ADR-034 결정 6

## 작업

### 1) 테스트 먼저 — 새 파일 `backend/tests/test_audit_api.py`

1. 관리자 세션으로 `GET /api/admin/audit` → 200, `items`가 `id` 내림차순(최신순). 각 항목에 `id, occurred_at, action, actor, actor_via, db_role, document_id, document_title, detail`.
2. `?actor=alice` → alice의 행만. `?action=document_deleted` → 그 동작만. 둘을 함께 주면 AND.
3. `?action=모르는값` → 422.
4. `?limit=2` → 2건과 `next_before_id`. `?before_id=<next_before_id>&limit=2` → 이어지는 2건(중복·누락 없음). 마지막 페이지의 `next_before_id`는 null. `limit` 기본 50, 1 미만·200 초과는 422.
5. 일반 사용자 세션 → 403, `detail == "관리자 권한이 필요합니다."`. 익명 → 401.
6. **관리자 본인의 API 토큰**으로 요청 → 403(세션 전용).
7. 관리자가 열람할 수 없는 **다른 사용자의 제한 문서**에 대한 기록도 보이고 `document_title`이 들어 있다. 응답 JSON 어디에도 그 문서의 본문 문자열이 없다(본문에 고유 문자열을 넣고 응답 텍스트에 없음을 단언).
8. 감사 로그를 조회해도 감사 행 수가 늘지 않는다.

### 2) 구현

**`services/audit.py`에 추가**

```python
AUDIT_ACTIONS: tuple[str, ...] = (...)  # 위 7개

async def list_audit(
    conn: psycopg.AsyncConnection,
    *,
    actor: str | None = None,
    action: str | None = None,
    limit: int = 50,
    before_id: int | None = None,
) -> list[dict]:
```

- SQL 한 문장. `WHERE (actor = %s OR %s IS NULL) AND … AND (id < %s OR %s IS NULL) ORDER BY id DESC LIMIT %s`. `occurred_at`은 트랜잭션 시작 시각이라 같은 트랜잭션의 행이 같은 값을 가진다 — 정렬·커서 기준은 `id`다.
- `detail`은 dict로 돌려준다.

**새 파일 `backend/openarchive/api/audit.py`** — `APIRouter(prefix="/api/admin/audit")`, `GET ""`, 의존성 `require_admin`. 쿼리 `actor: str | None`, `action: Literal[...] | None`(또는 검증해 422), `limit: int = Query(50, ge=1, le=200)`, `before_id: int | None`. 응답 `{"items": [...], "next_before_id": int | None}` — `len(items) == limit`일 때 마지막 항목의 `id`, 아니면 null.

**`api/schemas.py`** — `AuditEntry`, `AuditPage` 모델. **`main.py`** — 라우터 등록.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_audit_api.py tests/test_audit.py tests/test_main.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: ① `require_admin`을 `require_user_id`로 바꾸면 테스트 5·6 실패 ② `ORDER BY id DESC`를 `ASC`로 → 테스트 1 실패 ③ 커서 조건 `id < before_id`를 `<=`로 → 테스트 4 실패.
3. 아키텍처 체크: 응답에 본문이 없는가, 조회가 기록을 남기지 않는가.
4. `phases/m23-audit-log/index.json`의 step 7을 갱신한다. summary에 엔드포인트·쿼리 파라미터·응답 모양을 적는다(step 8이 쓴다).

## 금지사항

- 응답에 문서 본문·발췌·태그를 넣지 마라. 이유: 관리자에게 허용된 예외는 제목뿐이다(ADR-055 결정 8, ADR-040).
- 이 조회 결과를 검색·목록·집계 등 다른 API에 재사용하지 마라. 이유: 제목 예외는 감사 화면 하나에 한정된다.
- 토큰으로 열리게 하지 마라. 이유: `/api/admin/*`는 세션 전용이다(ADR-034 결정 6).
- 감사 행을 수정·삭제하는 API를 만들지 마라. 이유: DB가 거부하며 명세서상 변경 불가다.
- 기존 테스트를 깨뜨리지 마라
