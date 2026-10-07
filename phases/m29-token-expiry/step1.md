# Step 1: token-service

`services/auth.py`·`services/shares.py`에 만료 판정·마지막 사용 갱신·만료일 발급·목록 필드를 넣는다. 라우터·스키마는 step 2다.

## 공통 배경 — m29-token-expiry 설계 결정 (모든 step 같음)

이슈 #199. 근거 ADR-061 결정 1(채택 2026-10-06), 위임 토큰 ADR-034(결정 4 「2026-10-06 개정」 표기 있음), 공유 토큰 ADR-044 「공유」. 지금 위임 토큰과 공유 토큰은 **만료가 없고 마지막 사용 시각도 없다.** 이 phase는 둘에 **선택적 만료일**과 **마지막 사용 시각**을 더한다.

사실 (탐색으로 확인):

- 공유 토큰은 별도 테이블이 아니다. **`api_tokens`의 `share_id`가 있는 행**이다(026 — `api_tokens_one_principal`·`api_tokens_share_read_only`). 이슈 본문의 `share_tokens`는 없는 이름이다 — 컬럼은 `api_tokens` 한 곳에만 더한다.
- 토큰 해석 지점은 `backend/openarchive/services/auth.py`의 `validate_token` **하나**다. REST(`api/deps.py current_user`)와 원격 MCP(`mcp_server/http.py`의 Bearer 미들웨어 — `async with connection() as conn:` 블록이 끝나며 커밋)가 같은 함수를 쓴다. 만료 판정·마지막 사용 갱신은 여기에만 둔다.
- 발급 경로는 `insert_token` 하나(`create_token` = 사용자 토큰, `services/shares.py issue_share_token` = 공유 토큰).
- 사용자 CLI(#189)는 아직 없다. CLI `whoami` 만료일 표시는 #189 몫이다 — 이 phase에서 `/api/auth/me`를 바꾸지 않는다.

결정 (2026-10-07 사용자 승인):

- **D1 스키마** — `api_tokens`에 `expires_at timestamptz NULL`, `last_used_at timestamptz NULL`. NULL 만료 = 만료 없음(기본, 기존 토큰 하위 호환). `expires_at > created_at` 같은 CHECK는 두지 않는다 — 과거 시각 거부는 발급 서비스가 하고, 테스트·실측은 `UPDATE`로 만료를 앞당겨야 한다.
- **D2 만료 판정** — `validate_token`의 조회 SQL 자체에 `(t.expires_at IS NULL OR t.expires_at > now())`. 만료 토큰은 **폐기와 똑같이** `AuthenticationFailed` → REST는 익명(인증 필요 경로에서 401, 쿠키로 폴백하지 않음 — ADR-034 결정 5 그대로), 원격 MCP는 401 + `WWW-Authenticate: Bearer`. 만료 전용 문구를 두지 않는다(폐기·만료·틀린 값을 구별해 알려주지 않는다).
- **D3 마지막 사용 갱신** — 인증에 성공한 `validate_token` 호출이 같은 트랜잭션에서 갱신한다. **1분 미만 중복 갱신은 건너뛰고, 잠금을 기다리지 않는다**:
  `UPDATE api_tokens SET last_used_at = now() WHERE id = (SELECT id FROM api_tokens WHERE id = %s AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute') FOR UPDATE SKIP LOCKED)` 수준.
  이유: REST 요청 트랜잭션은 응답 직전까지 열려 있어, 그냥 `UPDATE`하면 그 행 잠금을 요청 내내 쥔다 — 같은 토큰의 동시 요청(MCP 병렬 호출, 수십 초 걸리는 `ask` 옆의 검색)이 앞 요청이 끝날 때까지 줄을 선다. 트레이드오프: 롤백된 요청(4xx·5xx로 예외가 난 요청)의 사용은 남지 않는다 — 「마지막 사용」은 **성공한 요청의** 마지막 사용이다.
- **D4 발급 입력** — `POST /api/auth/tokens`와 `POST /api/shares/{id}/tokens`의 body에 선택 필드 `expires_at`(시간대 있는 ISO 8601 시각, 생략·null = 만료 없음). 지금 이후가 아니면 **400** `"만료일은 지금 이후여야 합니다."`. 시간대 없는 값은 스키마가 거부한다(pydantic `AwareDatetime`, 422). API가 시각 단위로 받는 이유: 실측에서 "지금 + 1분"으로 발급해 만료를 바로 확인할 수 있다.
- **D5 목록 출력** — 사용자 토큰 목록(`GET /api/auth/tokens`)·공유 목록의 토큰(`GET /api/shares`의 `tokens[]`)·발급 응답에 `expires_at`, `last_used_at`, `expired: bool`을 싣는다. **`expired`는 서버가 DB `now()`로 판정한다**(브라우저 시계에 기대지 않는다). 만료된 행은 자동 삭제하지 않는다 — 목록에 「만료」로 남고, 지우는 것은 사람의 폐기다.
- **D6 화면** — 발급 폼에 날짜 입력 「만료일 (선택)」. 비우면 만료 없음. 고른 날짜는 **그날 끝까지 유효**하다 — 브라우저 로컬 시간대로 **다음 날 00:00**을 `expires_at`으로 보낸다. 명세서 "그 날짜가 지나면"과 맞춘다.
- **D7 `needs_vm=true`** — 모든 토큰 요청이 쓰기 하나를 동반하게 되므로 머지 전에 HA VIP 경유 REST·원격 MCP로 아래 TC를 실측한다. step 세션은 VM에 손대지 않는다.

**바꾸지 않는 것**: 세션 쿠키(`sessions.expires_at`)·로그인, 토큰 scope·공유 허용 목록(`require_reader`), 토큰 발급·목록·폐기의 세션 전용 경계(ADR-034 결정 6), 감사 로그(토큰 사용·만료는 감사 대상이 아니다 — ADR-055 결정 6), `/api/auth/me`, 관리자 공유 화면(#201).

### 기능명세서 시험항목 — 문구가 구현 계약이다 (글자 그대로 지킨다)

정본은 제출본 `notes/contest/submission/functional-spec-submit.md`(로컬 파일).

| 번호 | 중분류 | 소분류 | 시험 내용 |
|---|---|---|---|
| 270 | API 토큰 | 발급·폐기 | 새로고침하면 토큰 목록에 이름·범위·발급일만 남고 원문은 다시 표시되지 않음 (회귀 — 열이 늘어도 원문은 없다) |
| 271 | API 토큰 | 발급·폐기 | 토큰을 폐기하면 그 토큰으로 보낸 API 요청이 401로 거부됨 (회귀) |
| 272 | API 토큰 | 만료·마지막 사용 | 만료일을 정해 발급한 토큰은 그 날짜가 지나면 API 요청이 401로 거부되고 목록에 「만료」로 표시됨 |
| 273 | API 토큰 | 만료·마지막 사용 | 토큰으로 REST·원격 MCP·CLI 요청을 보내면 목록의 「마지막 사용」 시각이 갱신됨 (CLI는 #189 — REST를 쓰므로 자동 추종) |
| 276 | 외부 공유 | | 만료일을 정해 발급한 공유 토큰은 만료 뒤 요청이 401로 거부되고, 공유 화면에 「만료」와 마지막 사용 시각이 표시됨 |

화면 문구 「만료」·「마지막 사용」은 위 글자 그대로 쓴다.

## 읽어야 할 파일

- `/docs/ADR.md` — ADR-034(결정 4·5), ADR-044 「공유」, ADR-061 결정 1
- `backend/openarchive/migrations/034_token_expiry_tables.sql` — step 0 산출물
- `backend/openarchive/services/auth.py` — `insert_token`·`create_token`·`validate_token`·`list_tokens`·`revoke_token`
- `backend/openarchive/services/shares.py` — `issue_share_token`·`list_shares`(토큰 dict에 싣는 키 목록)
- `backend/openarchive/api/deps.py`, `backend/openarchive/mcp_server/http.py` — `validate_token`을 부르는 두 곳(바꾸지 않는다, 읽기만)
- `backend/openarchive/db.py` — 앱 풀은 `AsyncClientCursor`·non-autocommit
- `backend/tests/test_auth.py`, `backend/tests/test_shares.py`, `backend/tests/test_token_access.py` — 기존 토큰 테스트와 fixture

## 작업

### 1) 테스트 먼저 — `backend/tests/test_auth.py`·`backend/tests/test_shares.py`에 추가

실제 DB로 한다(Mock 금지). 만료는 `UPDATE api_tokens SET expires_at = now() - interval '1 second'`로 앞당긴다.

1. `create_token(..., expires_at=None)`(생략 포함)으로 발급한 토큰은 `expires_at`이 NULL이고 `validate_token`이 성공한다.
2. 미래 `expires_at`으로 발급하면 그 값이 저장되고 `validate_token`이 성공한다.
3. `expires_at`이 지금 이전이거나 지금과 같으면 발급이 `InvalidTokenExpiry`(아래)로 거부되고 행이 생기지 않는다 — 사용자 토큰·공유 토큰 둘 다.
4. 만료를 과거로 앞당긴 사용자 토큰·공유 토큰은 `validate_token`이 `AuthenticationFailed`를 낸다 — 폐기와 같은 예외 타입.
5. `validate_token` 성공 뒤 같은 트랜잭션을 커밋하면 `last_used_at`이 채워진다. 실패(틀린 값·만료)한 호출은 어떤 행의 `last_used_at`도 바꾸지 않는다.
6. 1분 안에 다시 성공하면 `last_used_at`이 바뀌지 않는다. `last_used_at`을 2분 전으로 `UPDATE`해 두면 다음 성공이 갱신한다.
7. **잠금을 기다리지 않는다(D3)**: 연결 A가 트랜잭션 안에서 그 토큰 행을 잠근 채(`SELECT ... FOR UPDATE` 또는 미커밋 UPDATE) 있을 때, 연결 B에서 `SET LOCAL lock_timeout = '1s'`를 걸고 `validate_token`을 부르면 **에러 없이 성공**한다(`last_used_at`이 오래돼 갱신 대상인 상태에서). A·B는 별도 연결이어야 한다.
8. `list_tokens`·`list_shares`의 토큰 dict에 `expires_at`·`last_used_at`·`expired`가 있다. `expired`는 만료 없음 → False, 미래 → False, 과거로 앞당김 → True. 만료된 토큰도 목록에서 사라지지 않는다. 원문·해시 키는 여전히 없다.
9. `create_token`·`issue_share_token`의 반환 dict에도 세 키가 있다(`last_used_at` None, `expired` False).

### 2) 구현

```python
class InvalidTokenExpiry(ValueError):
    """만료일이 지금 이후가 아니다."""   # services/auth.py — 메시지 "만료일은 지금 이후여야 합니다."

async def create_token(conn, user_id, *, name, scope=SCOPE_READ, expires_at: datetime | None = None) -> dict
async def insert_token(conn, *, user_id, share_id, name, scope, expires_at: datetime | None = None) -> dict
async def issue_share_token(conn, share_id, *, owner, name, expires_at: datetime | None = None) -> dict  # services/shares.py
```

- 과거 판정은 **DB `now()`** 로 한다(앱 서버 시계가 아니라). 예: `INSERT ... SELECT ... WHERE %(expires_at)s IS NULL OR %(expires_at)s > now()` 후 행이 없으면 예외, 또는 먼저 `SELECT %s > now()`. 방식은 재량.
- `validate_token`: 조회 WHERE에 D2 조건. 성공하면 D3의 SKIP LOCKED 갱신 한 문장을 같은 연결로 실행한다(토큰 `id`를 조회에서 함께 가져온다). 반환 dict의 기존 키·의미는 바꾸지 않는다 — `api/deps.py`·`mcp_server/http.py`가 그대로 쓴다. 새 키를 반환 dict에 더하지 마라(호출부가 쓰지 않는다).
- `expired`는 SQL에서 `t.expires_at IS NOT NULL AND t.expires_at <= now()`로 계산해 싣는다. `list_tokens`·`list_shares`·`insert_token` RETURNING 셋이 같은 정의를 쓴다.
- 모듈 머리말·함수 docstring의 "만료·폐기 상태를 따로 저장하지 않는다" 류 문장이 있으면 사실에 맞게 고친다(이 step에서 바꾼 함수에 한해).

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_auth.py tests/test_shares.py tests/test_token_access.py tests/test_share_access.py tests/test_mcp_http.py tests/test_auth_api.py tests/test_shares_api.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인 — 각각에서 테스트가 실패해야 한다. 확인 뒤 되돌린다.
   - `validate_token`의 만료 조건을 지운다 → 테스트 4 실패
   - 갱신 문장에서 `SKIP LOCKED`를 지운다 → 테스트 7 실패
   - 1분 조건을 지운다 → 테스트 6 실패
   - 과거 판정을 지운다 → 테스트 3 실패
3. 아키텍처 체크리스트: 만료 판정이 `validate_token` 한 곳에만 있는가(`api/deps.py`·`mcp_server/http.py`에 만료 코드가 없는가), 서버 바인딩(`prepare=True`)·임시 테이블·세션 `SET`을 쓰지 않았는가(CLAUDE.md).
4. `phases/m29-token-expiry/index.json`의 step 1을 갱신한다(summary에 새 시그니처·`InvalidTokenExpiry`·목록 dict 키).

## 금지사항

- `api/deps.py`·`mcp_server/http.py`에 만료·갱신 코드를 넣지 마라. 이유: 해석 지점은 `validate_token` 하나다(D2·D3).
- 마지막 사용 갱신을 별도 커넥션·별도 트랜잭션·백그라운드 태스크로 하지 마라. 이유: 풀 연결을 요청마다 하나 더 쓰고, 이슈 결정은 "같은 트랜잭션"이다. 잠금 대기는 SKIP LOCKED로 푼다.
- 세션 `SET lock_timeout`(LOCAL 없이)을 앱 코드에 쓰지 마라. 이유: OpenProxy 풀 백엔드를 타고 다음 클라이언트로 샌다(ADR-022). 테스트 안에서만 `SET LOCAL`.
- 만료된 토큰 행을 지우는 코드를 넣지 마라. 이유: D5 — 목록에 「만료」로 남아야 한다(TC 272·276).
- 라우터·스키마(`api/auth.py`·`api/shares.py`·`api/schemas.py`)를 건드리지 마라. 이유: step 2의 범위다.
- 기존 테스트를 깨뜨리지 마라
