# Step 4: token-access-proof

## 배경 — 각 step은 자기 단위를 검증했지만, 아무도 전 구간을 지나가지 않았다

step 0~3이 테이블·서비스·게이트·엔드포인트를 각각 테스트와 함께 만들었다. 하지만 **step
경계를 가로지르는 시나리오는 아직 한 번도 실행되지 않았다** — 발급받은 토큰 하나로 문서를
공급하고, 검색하고, 조회하는 전 구간 말이다. 이 phase의 직전 작업(m11-a)에서 각 step이 전부
통과했는데도 step 경계에 결함이 남은 사례가 있었다.

이 step은 이슈의 Acceptance Criteria를 **행동 테스트 한 파일**로 모으고, 공급 예제가 사람의
비밀번호 없이 동작하도록 만든다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

### 결정 1 — 테스트는 `backend/tests/test_token_access.py` 한 파일에 모은다

이슈 AC를 심사·리뷰에서 한 곳에서 확인할 수 있어야 한다. 각 파일에 흩으면 "AC가 전부
검증됐는가"를 사람이 조립해야 한다.

### 결정 2 — R4는 "헤더에 이름을 써도 무시된다"가 아니라 두 명제로 검증한다

이 phase가 지켜야 할 `docs/PRD.md` §6 R4는 정확히 이것이다:
**주체는 서버가 발급·보관한 자격증명의 검증으로만 해석되며, 검증되지 않은 식별자만으로는
어떤 주체도 될 수 없다.**

따라서 테스트는 두 방향으로 쓴다.

- **음성** — 검증을 통과하지 못한 어떤 입력도 주체를 만들지 못한다. 사용자명 그대로
  (`Bearer alice`), 임의 문자열, 폐기된 토큰, 유효 토큰의 한 글자 변조, **DB에 저장된
  `token_hash` 값 자체**를 제시해도 전부 미인증이다. 마지막 항목이 특히 중요하다 —
  해시를 알아도 원문이 아니면 통과하지 못한다는 것이 해시 저장의 실질적 의미다.
- **양성** — 유효한 토큰은 **오직 발급자로만** 해석된다. 토큰으로 만든 문서의 소유자,
  `/api/auth/me`, 문서 목록의 열람 범위가 전부 발급자 기준이다. 요청 본문에 다른 사람의
  식별자를 실어도 결과가 달라지지 않는다.

### 결정 3 — 세션 전용 경계는 여기서 다시 교차 검증한다

`read_write` 토큰으로 `/api/auth/tokens`(발급·목록·폐기)와 `/api/admin/users`(생성·목록·삭제)에
접근할 수 없음을 이 파일에서 확인한다. step 2·3이 각각 자기 자리에서 확인했지만, **최고 권한
토큰(`read_write`, 게다가 관리자 계정이 발급한)이 관리 경계를 넘지 못한다**는 것은 이 phase의
안전 주장 자체라 교차 검증이 필요하다.

### 결정 4 — 예제는 `--token`을 얻고, 표준 라이브러리 독립성을 유지한다

`examples/ingest_text.py`가 토큰으로 동작하면 `docs/ROADMAP.md` 1단계의 성공 기준("외부
프로세스가 `backend`를 import하지 않고 HTTP만으로 공급")이 **사람의 비밀번호에 묶이지 않은
형태로** 성립한다. `backend` import 금지와 표준 라이브러리 제한은 그대로다
(`backend/tests/test_architecture.py`가 단언한다).

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- **이 phase의 이전 step 산출물 전부** — `backend/migrations/013_token_tables.sql`,
  `backend/app/services/auth.py`, `backend/app/api/deps.py`, `backend/app/api/auth.py`,
  `backend/app/api/schemas.py`, `backend/app/api/documents.py`
- `backend/tests/conftest.py` — `db_client`·`migrated_db` fixture, `login_as`,
  `run_embedding_worker`, `insert_test_document`. **워커를 언제 돌릴지는 테스트가 정한다**
  (:179-190의 주석 참조)
- `backend/tests/test_documents_api.py` — 3자(익명·타인·소유자) 열람 테스트의 기존 형태
  (:340 근처)
- `backend/tests/test_search_api.py` — 검색 요청 형태(`POST /api/search`)와 익명 거부 테스트
- `examples/ingest_text.py` — **수정 대상.** 112줄 전부 읽어라
- `backend/tests/test_ingest_text.py` — 예제의 계약을 고정하는 테스트. `--token` 추가에 맞춰
  갱신한다
- `backend/tests/test_architecture.py`:72-92 — 예제의 import 독립성 검사. **깨뜨리면 안 된다**
- `docs/PRD.md` §6 — R3·R4·IA-2·IA-3의 원문
- `docs/ADR.md` 의 **ADR-027**(볼 수 없는 문서는 존재하지 않는 것처럼 보인다) — 3자 열람
  테스트가 지켜야 할 의미론

## 작업

### 1) `backend/tests/test_token_access.py`(신규)를 쓴다

`db_client`를 쓰되, **토큰 경로 테스트는 반드시 쿠키를 비운 상태에서 실행한다**
(`db_client.cookies.clear()`). 쿠키가 남아 있으면 무엇이 요청을 통과시켰는지 알 수 없어
테스트가 거짓 통과한다.

**(a) 토큰만으로 공급 → 검색 → 조회 완주** (이슈 AC 1)

`read_write` 토큰 하나로 쿠키 없이: `POST /api/documents/text`로 공급 → `run_embedding_worker`로
잡 처리 → `GET /api/documents/{id}`가 `ready` → `POST /api/search`가 그 문서를 찾는다.
공급된 문서의 `owner_id`가 **토큰 발급자**임을 확인한다.

**(b) `read` 토큰의 경계** (이슈 AC 3)

쓰기 6종 전부 403, 읽기(목록·상세·검색)는 200.

**(c) 폐기의 독립성** (이슈 AC 2)

한 사용자가 토큰 A·B와 로그인 세션을 함께 가진 상태에서 A를 폐기한다. A는 즉시 401,
**B와 세션은 계속 200**이다.

**(d) 3자 열람이 토큰 경로에도 성립한다** (이슈 AC 5)

alice의 `private` 문서에 대해: alice의 토큰은 조회 성공, bob의 토큰은 **없는 것처럼**
실패(404 — 403이 아니다), 토큰·쿠키 없는 요청은 401. `public` 문서는 bob의 토큰으로도
보인다. 검색 결과와 문서 목록에서도 같은 범위가 적용된다 — 상세 조회만 막고 목록에 제목이
보이면 ADR-027 위반이다.

**(e) R4 — 검증되지 않은 식별자로는 주체가 되지 않는다** (이슈 AC 4, 결정 2)

음성: 아래 각각을 `Authorization: Bearer <값>`으로 보내면 **전부 미인증**이다.
사용자명 그대로(`alice`), 임의 문자열, 폐기된 토큰, 유효 토큰의 마지막 한 글자를 바꾼 값,
그리고 **`api_tokens.token_hash`에 저장된 해시 문자열 자체**(DB에서 읽어 그대로 제시한다).

양성: 유효한 토큰은 발급자로만 해석된다. bob의 토큰으로 문서를 공급하면서 요청 본문에
`owner_id`/`username` 같은 필드를 끼워 넣어도 **소유자는 bob이다**(무시되거나 422 —
어느 쪽이든 alice의 문서가 되지 않는다는 것이 요점이다).

**(f) 최고 권한 토큰도 관리 경계를 넘지 못한다** (결정 3)

**관리자 계정(`is_admin=True`)이 발급한 `read_write` 토큰**으로 아래를 호출하면 전부 **403**이다.
`POST /api/auth/tokens` · `GET /api/auth/tokens` · `DELETE /api/auth/tokens/{id}` ·
`POST /api/admin/users` · `GET /api/admin/users` · `DELETE /api/admin/users/{id}`.
같은 관리자의 **로그인 세션으로는 같은 호출이 성공**함을 대조군으로 함께 단언한다 —
403이 권한 부족이 아니라 자격증명 종류 때문임을 보이는 자리다.

### 2) `examples/ingest_text.py`에 `--token`을 추가한다

- `--token`과 `--username/--password`는 **상호 배타**다. 둘 다 없거나 둘 다 있으면 인자
  오류로 즉시 종료한다 (`argparse`의 상호 배타 그룹 또는 명시적 검사 — 재량).
- 토큰을 쓰면 로그인 요청을 **보내지 않고**, 모든 요청에 `Authorization: Bearer <token>`을
  붙인다. 쿠키 처리는 그대로 둬도 되지만 토큰 경로에서 쿠키에 의존해선 안 된다.
- 표준 라이브러리만 쓴다. 새 import는 필요 없다.
- 모듈 상단의 docstring 실행 예시에 토큰 사용법을 한 줄 추가한다.

`backend/tests/test_ingest_text.py`를 갱신한다 — 토큰이 주어지면 `LOGIN_PATH`를 호출하지
않는다는 것, 요청에 `Authorization` 헤더가 붙는다는 것, 두 인증 방식을 함께 주면 거부된다는
것을 고정한다.

## Acceptance Criteria

```bash
cd backend

# 1) 이슈 AC 전체가 한 파일에서 통과한다
.venv/bin/pytest tests/test_token_access.py -v
#   → 전부 passed. -v로 어떤 시나리오가 실행됐는지 이름을 눈으로 확인하라

# 2) 예제 계약과 독립성
.venv/bin/pytest tests/test_ingest_text.py tests/test_architecture.py -q
#   → 전부 passed

# 3) 전체 백엔드 회귀
.venv/bin/pytest -q
#   → 전부 passed

# 4) 예제가 backend를 import하지 않는다 (독립 실행 확인)
cd .. && python3 -c "import ast,sys; ast.parse(open('examples/ingest_text.py').read())" && \
  python3 examples/ingest_text.py --help
#   → --help가 정상 출력되고 --token이 목록에 보인다.
#     (이 명령은 backend/.venv가 아니라 시스템 python3로 돈다 — 의존성 독립의 실증이다)

# 5) 전체 검증
bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 토큰 테스트가 **쿠키를 비운 상태**에서 도는가? (남아 있으면 거짓 통과한다)
   - 3자 열람에서 타인의 private 문서가 **404**로 떨어지는가? 403은 존재를 누설한다 (ADR-027)
   - 테스트를 통과시키려고 게이트나 열람 술어를 약화하지 않았는가? 실패하면 **구현이 틀린
     것**이므로 이전 step의 코드를 고쳐라
   - `examples/`에 서드파티 import가 들어가지 않았는가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 5에서 일괄 처리).
4. 결과에 따라 `phases/m11c-token-auth/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **테스트를 통과시키려고 검증을 약화하지 마라.** 이유: `CLAUDE.md`의 개발 프로세스 규칙.
  실패하는 단언을 지우거나 skip하거나, 403을 기대하던 곳을 200으로 고치지 마라. 이 step은
  이전 step의 구현이 실제로 맞는지 보는 자리다.
- **`scripts/demo_recovery.sh`를 고치지 마라.** 이유: 이번 phase의 범위 밖으로 결정됐다.
  그 스크립트는 문서를 만들고 지우므로 어차피 로그인이 필요하고, 쿠키 경로가 이미 정상
  동작하며, 실행 검증에 VM이 필요하다.
- **예제에 재시도·백오프·에러 포매팅 같은 기능을 덧붙이지 마라.** 이유: 예제는 HTTP 경로를
  보여주는 것이 목적이다. `--token` 하나만 추가한다.
- **`--token`을 환경변수로도 읽게 만들지 마라.** 이유: 요청받지 않은 설정 가능성이다.
- **프론트엔드를 고치지 마라.** 이유: 토큰 관리 UI는 명시적 non-goal이다.
- **새 엔드포인트를 만들지 마라.** 이유: 이 step은 검증과 예제뿐이다. 테스트가 없는 기능을
  요구하면 구현이 아니라 테스트가 잘못된 것이다.
- **기존 테스트를 깨뜨리지 마라.**
