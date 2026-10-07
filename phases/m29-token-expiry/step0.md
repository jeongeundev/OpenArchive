# Step 0: token-columns

`api_tokens`에 `expires_at`·`last_used_at` 컬럼을 더하는 마이그레이션 `034_token_expiry_tables.sql`과 그 테이블 테스트만 만든다. 서비스·API·화면은 다음 step이다.

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

- `/docs/ADR.md` — ADR-034 결정 4(「2026-10-06 개정」 표기), ADR-061 결정 1
- `backend/openarchive/migrations/013_token_tables.sql`, `backend/openarchive/migrations/026_shares_tables.sql`
- `backend/openarchive/migrations/` 최신 번호(033이 마지막이어야 한다 — 034가 이미 있으면 다음 빈 번호를 쓰고 summary에 적는다)
- `backend/tests/test_tables.py` — 기존 `api_tokens` 테스트(`test_api_token_names_exactly_one_principal` 등)와 fixture 사용법
- `backend/tests/conftest.py` — 마이그레이션 적용 방식

## 작업

### 1) 테스트 먼저 — `backend/tests/test_tables.py`에 추가

1. 컬럼을 생략하고 사용자 토큰·공유 토큰을 INSERT하면 `expires_at`·`last_used_at`이 둘 다 NULL이다(기존 발급 SQL 하위 호환).
2. 두 컬럼의 타입이 `timestamp with time zone`이고 NULL을 허용한다(`information_schema.columns`).
3. `expires_at`을 `created_at`보다 **과거**로 `UPDATE`할 수 있다 — D1(CHECK 없음). 실측·테스트가 만료를 앞당기는 경로다.

### 2) 구현 — `backend/openarchive/migrations/034_token_expiry_tables.sql`

- 머리 주석: 무엇을 왜(ADR-061 결정 1, #199), NULL = 만료 없음, 과거 거부는 발급 서비스 몫이라 CHECK를 두지 않는 이유, `last_used_at`은 `validate_token`이 1분 단위로 갱신한다는 점.
- `ALTER TABLE api_tokens ADD COLUMN expires_at timestamptz, ADD COLUMN last_used_at timestamptz;` — 기본값 없음.
- 인덱스를 만들지 않는다. 조회는 `token_hash` UNIQUE와 `id` PK로만 한다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_tables.py tests/test_auth.py tests/test_token_access.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: 스키마 변경이 번호 붙은 raw SQL 하나인가(ORM 도구 금지), 013·026을 수정하지 않았는가(적용된 마이그레이션은 제자리에서 고치지 않는다).
3. `phases/m29-token-expiry/index.json`의 step 0을 갱신한다(summary에 마이그레이션 파일명과 컬럼 정의).

## 금지사항

- 기존 마이그레이션 파일(013·026 등)을 고치지 마라. 이유: 이미 적용된 DB(VM·HA)에는 다시 돌지 않는다.
- `expires_at > created_at` 같은 CHECK를 넣지 마라. 이유: D1 — 테스트·실측이 `UPDATE`로 만료를 앞당긴다.
- `share_tokens` 테이블을 만들지 마라. 이유: 공유 토큰은 `api_tokens` 행이다.
- 서비스·API 코드를 건드리지 마라. 이유: step 1·2의 범위다.
- 기존 테스트를 깨뜨리지 마라
