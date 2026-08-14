# Step 4: contract-tests

## 배경 — 규칙은 문서에 있는데 강제하는 장치가 없다

ADR-027은 열람 범위의 강제 수단을 *"grep이 아니라 행동 테스트"*로 못박았고, `CLAUDE.md`는
파생 테이블 직접 INSERT 금지와 계층 경계를 CRITICAL로 적어 뒀다. 그런데 넷은 자동 보증이 없다.
지금 통과하는 것은 규율의 결과일 뿐, 다음 사람이 어겨도 아무것도 울리지 않는다.

| # | 공백 | 지금 상태 |
|---|---|---|
| a | `services/diagnostics.py:73-113` `DUPLICATES_SQL`의 열람 범위 | 진단 4종 중 이 쿼리만 3자 행동 테스트가 없다. `test_visibility.py:357`은 `related.py`의 `IDENTICAL_SQL`을 보는 것으로, 다른 쿼리다 |
| b | 세션 독립 폐기 (PRD R3) | `logout`이 `WHERE token = %s`로 **한 세션만** 지운다는 사실을 못박는 테스트가 없다 |
| c | 파생 테이블 직접 INSERT 금지 | `CLAUDE.md` 서술과 코드 리뷰에만 의존한다 |
| d | 계층 경계 (services에 HTTP 없음) | 현재 0건이지만 자동 검사가 없다 |

이 step은 **구현을 바꾸지 않는다.** 지금 옳은 것이 앞으로도 옳게 유지되도록 잠그는 작업이다.
테스트가 처음부터 통과하는 것이 정상이다 — 다만 **일부러 깨뜨려 빨간불을 한 번 보고** 되돌려,
그 테스트가 실제로 판정하는지 확인해야 한다.

## 읽어야 할 파일

- `backend/app/services/diagnostics.py:73-113` — `DUPLICATES_SQL`. `visible_documents` CTE
  하나로 열람 범위를 걸고, `identical`과 `overlap_pairs` 양쪽이 그 CTE를 조인한다. 즉 **쌍의
  양끝이 모두 가시**여야 결과에 나온다
- `backend/tests/test_visibility.py` — (a)를 넣을 곳. `visible_documents` fixture와 3자 호출
  관례(`anonymous`/`other_user`/`owner`)
- `backend/tests/test_diagnostics.py` — 중복 진단 테스트가 `identical`·`overlaps` 상태를 어떻게
  만드는지. (a)의 fixture 설계에 그대로 쓴다
- `backend/app/services/auth.py:143-183` — `create_session`·`validate_session`·`logout`.
  `logout`은 토큰 하나만 지운다
- `backend/tests/test_auth.py:77` — (b)를 넣을 곳. `logout` 서비스 테스트의 기존 형태
- `backend/migrations/009_auth_tables.sql:22-27` — `sessions` 테이블. PK는 `token`이고
  `user_id`는 FK다
- `docs/PRD.md` R3 — *"자격증명은 주체와 분리되고, 한 주체가 여러 자격증명을 가질 수 있으며,
  **각각 독립적으로 폐기 가능**해야 한다"*
- `CLAUDE.md` 아키텍처 규칙 — 파생 테이블 INSERT 금지, 서비스 계층 규칙

## 작업

### (a) 중복 진단의 열람 범위를 3자 행동 테스트로 고정한다

`backend/tests/test_visibility.py`에 추가한다. **서비스(`get_diagnostics`)를 직접 호출한다.**

`identical`과 `overlaps` **둘 다** 본다. 한쪽만 보면 `DUPLICATES_SQL`의 절반이 그대로 비어
있는 셈이다.

- `identical` — 같은 `content_hash`를 갖는 두 문서를 만든다. 하나는 public(alice), 하나는
  private(alice). 소유자는 쌍을 보고, 익명과 타인은 **쌍 자체가 없다**(한쪽 끝이 비가시).
  자리 표시자도 남지 않아야 한다 — `total` 카운트에도 잡히면 안 된다 (ADR-027).
- `overlaps` — `document_edges`의 `overlaps` edge가 필요하므로 실제 청크·임베딩이 있어야 한다.
  `conftest.process_all_embedding_jobs`로 잡을 처리하고, 겹치는 내용의 문서 쌍을 만드는 방법은
  `test_diagnostics.py`의 기존 중복 테스트를 그대로 참고하라. 임계는
  `diagnostics.DUPLICATE_OVERLAP_RATIO`(0.95)다.

**`total`까지 단언하라.** `DUPLICATES_SQL`은 `count(*) OVER (PARTITION BY match_type)`으로 총
개수를 따로 내보낸다. 목록만 비고 숫자가 남으면 "몇 건 있다"는 사실이 새는 것이고, 그것이
ADR-027이 금지하는 누출이다.

### (b) 세션은 각각 독립적으로 폐기된다

`backend/tests/test_auth.py`에 추가한다. 서비스 직접 호출.

같은 사용자로 세션 두 개를 만들고, 하나만 `logout` 한 뒤:

- 폐기한 토큰은 `AuthenticationFailed`
- 나머지 토큰은 여전히 유효하고 **같은 사용자**를 돌려준다

테스트 이름이 계약을 말하게 쓴다. 예:
`test_logout_revokes_only_the_given_session_not_the_others`.

### (c)(d) 아키텍처 fitness test

`backend/tests/test_architecture.py`(신규) 하나에 둘 다 넣는다. DB가 필요 없는 정적 검사다.

**(c) 파생 테이블 직접 INSERT 금지**

- 스캔 대상: `backend/app/`, `backend/mcp_server/`, `scripts/`의 `.py` 파일
- 금지 패턴: `INSERT INTO embedding_jobs | document_versions | document_edges | document_links`
  (대소문자·공백 유연하게)
- **`backend/migrations/`를 스캔하지 마라.** 파생 행을 만드는 주체가 바로 그 트리거들이다.
  거기에 INSERT가 있는 것이 정상이며, 스캔하면 이 테스트는 영원히 실패한다.
- **`backend/tests/`도 스캔하지 마라.** 테스트가 상태를 직접 만드는 것은 별개 문제이고, 더
  중요하게는 **검사 범위에 테스트를 넣으면 이 테스트 파일 자신의 패턴 문자열이 걸린다.**
- 실패 메시지에 **파일 경로와 줄 번호**를 담아라. "어딘가 위반이 있다"로는 고칠 수 없다.

**(d) 서비스 계층에 HTTP 의존이 없다**

- 대상: `backend/app/services/*.py`
- 금지: `fastapi`·`starlette` **import**
- 🔴 **문자열 검색으로 하지 마라.** `app/services/documents.py:4`의 docstring에 *"MCP 서버는
  HTTPException을 쓸 수 없으므로 이 경계가 필요하다"*라는 문장이 있다. grep은 이 주석을 위반으로
  잡는다. `ast.parse`로 파일을 읽고 `ast.Import`/`ast.ImportFrom` 노드만 검사하라.
- 실패 메시지에 모듈명과 import 대상을 담아라.

### 마지막 — 빨간불을 한 번 본다

네 테스트 모두 **처음부터 통과할 것이다.** 그래서 각각을 한 번씩 일부러 깨뜨려 실패를 확인하고
되돌려라. 초록만 보고 "동작한다"고 판단하지 마라.

- (a) `diagnostics.py`의 `visible_documents` CTE에서 `WHERE {VISIBLE_TO_USER}`를 잠시 지운다 →
  익명·타인 단언이 깨져야 한다
- (b) `auth.logout`의 `WHERE token = %s`를 `WHERE user_id = (SELECT user_id FROM sessions WHERE token = %s)`로
  잠시 바꾼다 → 두 번째 토큰 단언이 깨져야 한다
- (c) 아무 서비스 파일에 `-- INSERT INTO embedding_jobs` 한 줄을 주석으로 넣는다 → 잡혀야 한다
  (주석 안이라도 잡는 것이 이 검사의 의도다. 실행되는 SQL과 주석을 구별하려 들지 마라 — 복잡도만
  늘고 회피 경로가 생긴다)
- (d) 아무 서비스 파일에 `from fastapi import HTTPException`을 잠시 넣는다 → 잡혀야 한다

**확인 후 반드시 원상 복구하라.** 되돌린 뒤 `git status`로 의도한 파일만 변경되었는지 확인한다.

## Acceptance Criteria

```bash
cd backend

# 1) 새 테스트 4종이 전부 통과한다
.venv/bin/pytest tests/test_architecture.py -q
#   → 2 passed 이상

.venv/bin/pytest tests/test_visibility.py tests/test_auth.py -q
#   → 전부 passed

# 2) 중복 진단 테스트가 identical과 overlaps를 둘 다 본다
grep -c "overlaps" tests/test_visibility.py
#   → 1 이상

# 3) fitness test가 ast를 쓴다 (문자열 검색이 아니다)
grep -c "ast" tests/test_architecture.py
#   → 1 이상

# 4) 스캔 범위에 migrations와 tests가 없다
grep -n "migrations\|tests" tests/test_architecture.py || echo "0건 — 스캔 범위 올바름"
#   → migrations/tests를 스캔 대상으로 넣은 줄이 없어야 한다
#      (경로 문자열로 제외를 명시한 주석은 무방하다 — 사람이 눈으로 판단하라)

# 5) 빨간불 확인의 흔적: 되돌린 뒤 작업 트리가 깨끗한가
cd .. && git status --short
#   → 이 step에서 만든 테스트 파일과 index.json만 나와야 한다.
#      app/services/*.py 가 수정된 채로 남아 있으면 원복을 빠뜨린 것이다

# 6) 전체 검증
bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. **네 테스트를 각각 일부러 깨뜨려 실패를 확인하고 되돌렸는지** 스스로 점검한다. 이 절차를
   건너뛰면 이 step은 아무것도 보증하지 않는다.
3. 아키텍처 체크리스트를 확인한다:
   - 테스트가 구현을 흉내 내지 않고 **행동**을 보는가? (SQL 문자열을 비교하는 테스트를 쓰지 마라)
   - `assert True`·예외만 잡고 검증 없는 패턴이 없는가?
   - 실패 메시지가 위반 위치를 알려주는가?
4. 결과에 따라 `phases/m11a-contract-seal/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **구현을 바꾸지 마라.** 이유: 이 step은 잠그는 작업이다. 테스트가 실패하면 그것이 진짜 결함이며,
  그때는 멈추고 `error`로 보고하라 — 조용히 고치면 결함이 있었다는 사실이 사라진다.
- **`backend/migrations/`를 (c)의 스캔 범위에 넣지 마라.** 이유: 파생 행을 만드는 주체가 그
  트리거들이다. 넣으면 테스트가 영원히 실패한다.
- **`backend/tests/`를 (c)의 스캔 범위에 넣지 마라.** 이유: 검사 범위에 테스트를 넣으면 이
  파일 자신의 패턴 문자열이 걸리고, 그것을 피하려고 문자열을 쪼개는 회피가 시작된다.
- **(d)를 grep·정규식으로 구현하지 마라.** 이유: `services/documents.py:4`의 docstring이
  "HTTPException"을 언급한다. 문자열 검색은 이 주석을 위반으로 잡는다.
- **빨간불 확인을 생략하지 마라.** 이유: 처음부터 통과하는 테스트는 아무것도 검사하지 않아도
  통과한다. 실패를 한 번 봐야 판정 능력이 확인된다.
- **깨뜨린 코드를 되돌리는 것을 잊지 마라.** 이유: `git status`에 서비스 파일이 남으면 다음
  step이 오염된 코드 위에서 시작한다.
- **DB에 의존하는 fitness test를 만들지 마라.** 이유: (c)(d)는 정적 검사다. DB를 끌어들이면
  느려지고, 검사 대상과 무관한 이유로 실패한다.
- **기존 테스트를 깨뜨리지 마라.**
