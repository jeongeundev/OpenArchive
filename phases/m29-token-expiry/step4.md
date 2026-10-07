# Step 4: token-docs

토큰 만료일·마지막 사용 시각 구현을 운영·아키텍처·ADR·PRD·UI 문서에 반영한다. 코드는 바꾸지 않는다.

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

- step 0~3 산출물: `backend/openarchive/migrations/034_token_expiry_tables.sql`, `backend/openarchive/services/auth.py`(`validate_token`·`InvalidTokenExpiry`), `backend/openarchive/api/schemas.py`, `frontend/src/app/settings/page.tsx`, `frontend/src/components/SharesSection.tsx`, `frontend/src/lib/tokenExpiry.ts`
- `docs/OPERATIONS.md` — 「API 토큰」 절(약 589행)·외부 공유 관련 절·원격 MCP 절
- `docs/ARCHITECTURE.md` — `api_tokens` 스키마(약 304행)와 인증 설명
- `docs/ADR.md` — ADR-034 결정 4의 「2026-10-06 개정」 표기, ADR-034 「남은 한계」 3번, ADR-044 「공유」 트레이드오프, ADR-061 상태 줄
- `docs/PRD.md` — 약 415행 「결정·미구현」 표기가 있는 문단과 기능 표
- `docs/UI_GUIDE.md` — `/settings` 절(약 102행)·외부 공유 절(약 134행)

## 작업

1. `docs/OPERATIONS.md` 「API 토큰」 절에 만료·마지막 사용 단락:
   - 만료일은 선택이며 기본 없음. 화면은 날짜(그날 끝까지 유효), API는 시간대 있는 시각(`expires_at`). 과거는 400.
   - 만료된 토큰은 폐기와 같은 401, 목록에 「만료」로 남는다 — 지우는 것은 폐기.
   - 「마지막 사용」은 인증에 성공해 **커밋된** 요청 기준이며 1분 단위로 갱신된다. 같은 토큰의 동시 요청을 줄 세우지 않으려고 잠긴 행은 건너뛴다(`SKIP LOCKED`) — 그 순간의 갱신은 다음 요청으로 미뤄진다.
   - 공유 토큰도 같다. 사용자 CLI `whoami`의 만료일 표시는 #189에서 생긴다는 말은 쓰지 마라(미래 약속) — 대신 지금 사실만 적는다.
   - 실측 팁: `curl`로 `expires_at`을 "지금 + 1분"으로 발급해 만료를 확인할 수 있다(예시 명령 하나).
2. `docs/ARCHITECTURE.md` `api_tokens` 스키마 블록에 두 컬럼(034)과 한 줄 설명, 인증 흐름 설명에 만료 판정·갱신 위치(`validate_token` 하나, REST·원격 MCP 공용).
3. `docs/ADR.md`:
   - ADR-034 결정 4의 「2026-10-06 개정」 인용 블록 끝에 "구현 #199(2026-10-07, 034)"와 D3의 SKIP LOCKED·롤백 요청 미기록 트레이드오프를 한두 문장으로.
   - ADR-034 「남은 한계」 3번에 표기 줄 추가: 만료(선택)와 마지막 사용 시각은 #199로 들어왔고, 토큰별 사용 **이력**은 여전히 남기지 않는다.
   - ADR-044 「공유」 트레이드오프 중 만료·사용 기록을 "후속 결정"으로 남긴 문장이 있으면 그 옆에 같은 표기.
   - ADR-061 상태 줄의 "구현 #199(결정 1)"에 「구현됨」 표기.
4. `docs/PRD.md` 해당 문단의 「결정·미구현」 표기를 구현 사실로 바꾼다(#199만 — 같은 문단의 #200·#201 표기는 건드리지 않는다).
5. `docs/UI_GUIDE.md` `/settings` 절과 외부 공유 절에 「만료일 (선택)」 입력·「만료」·「마지막 사용」 표시 규칙(서버 `expired`로 판정, 자정 만료는 전날 「까지」로 표시).

문구 규칙: "항상 최신"·"실시간"을 쓰지 않는다. 모든 문장은 실제 코드와 대조한다 — 코드에 없는 동작을 적지 마라.

## Acceptance Criteria

```bash
grep -n "034" docs/ARCHITECTURE.md | grep -i "expires_at\|last_used_at\|api_tokens"
grep -n "last_used_at\|마지막 사용" docs/OPERATIONS.md docs/UI_GUIDE.md
grep -n "#199" docs/ADR.md docs/PRD.md
cd backend && .venv/bin/pytest tests/test_architecture.py -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다(각 grep이 1줄 이상 출력해야 한다).
2. 쓴 문장마다 대응 코드(파일·함수)를 확인했는가 — 특히 1분 단위·SKIP LOCKED·자정 표시 규칙.
3. `phases/m29-token-expiry/index.json`의 step 4를 갱신한다.

## 금지사항

- 코드 파일을 고치지 마라. 이유: 문서 step이다 — 문서와 코드가 어긋나면 문서를 코드에 맞추고, 코드 결함은 `error_message`로 보고한다.
- ADR 본문의 과거 결정 문장을 지우거나 고쳐 쓰지 마라. 이유: 개정은 표기(인용 블록)로 덧붙이는 것이 이 저장소의 관례다.
- #200·#201 등 다른 이슈의 「미구현」 표기를 건드리지 마라. 이유: 범위 밖.
- 기존 테스트를 깨뜨리지 마라
