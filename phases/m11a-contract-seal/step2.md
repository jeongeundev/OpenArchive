# Step 2: status-auth

## 배경 — 무인증 status가 노드 주소와 정합성 카운트를 흘린다

`GET /api/system/status`는 지금 로그인 없이 다음을 반환한다: 접속 중인 DB 노드의 주소·포트
(`inet_server_addr()`), 임베딩 잡 카운터 4종, 정합성 어긋남 문서 수, 임베딩 프로바이더 이름.

ADR-028은 **읽기 엔드포인트 전부에 `require_user_id`를 요구**하기로 했다 — 검색·문서 목록·문서
상세·관련 문서·태그 추천·주제 덩어리·문서 진단. `status`만 예외로 남았고,
`backend/tests/test_system_api.py:12`가 그 예외를 *계약으로* 고정하고 있다
(`test_status_is_available_without_authentication_and_reports_operational_fields`).

이 step은 그 예외를 닫는다. 배포 토폴로지와 데이터 상태는 인증된 사용자에게만 보인다.

### ✅ 결정 1 — `/api/health`는 무인증으로 남는다

**이 결정은 이미 닫혔다. 다시 판단하지 말고 아래대로 진행하라.**

`backend/app/main.py:79-81`의 `/api/health`는 `{"status": "ok"}`만 반환한다. 문서도, 토폴로지도,
카운트도 없다. 로드밸런서·컨테이너 오케스트레이터·기동 대기 스크립트가 인증 없이 물어야 하는
표면이므로 그대로 둔다.

### 🔴 결정 2 — `demo_recovery.sh`의 `api_ready`는 `/api/health`로 옮긴다

**이것이 이 step에서 가장 깨지기 쉬운 지점이다.**

`scripts/demo_recovery.sh:113-115`의 `api_ready()`가 `/api/system/status`를 프로브하는데, 이
함수는 **로그인보다 먼저** 실행된다:

```
L284  wait_until 30 "API 기동" api_ready || fail "API가 기동하지 않았습니다"
L285  ensure_demo_session          # ← 쿠키는 여기서 처음 생긴다
```

status에 401을 걸고 `api_ready`를 그대로 두면 `curl -fsS`가 401에서 실패하고, 데모는 30초 뒤
*"API가 기동하지 않았습니다"*로 죽는다. **쿠키를 붙이는 것으로는 고칠 수 없다 — 그 시점에
쿠키가 없다.**

DB 준비는 이미 앞에서 확인된다(`L275`가 `db_value inconsistent`로 VM DB를 직접 조회한다).
그리고 마이그레이션은 API의 lifespan에서 돌므로(ADR-012), `/api/health`가 200을 주는 시점은
곧 앱이 완전히 기동한 시점이다. 따라서 `api_ready`를 `/api/health`로 옮겨도 데모가 기다리던
의미는 유지된다.

나머지 status 호출부(`jobs_drained` L121, `consistent` L126, 직접 호출 L305·L306)는 **전부
`ensure_demo_session` 이후**에 실행되므로 쿠키 재생으로 해결된다.

## 읽어야 할 파일

- `backend/app/api/system.py` — step 1에서 위임만 남은 상태. 여기에 의존성을 건다
- `backend/app/api/deps.py:36-42` — `require_user_id`. 익명이면 401을 던진다. **반환값은
  UUID가 아니라 username 문자열**이다
- `backend/app/api/diagnostics.py` — 읽기 엔드포인트에 `require_user_id`를 거는 기존 방식
- `backend/tests/test_system_api.py` — 전량 교체 대상. 특히 `:12`의 무인증 계약
- `backend/tests/test_diagnostics.py:31` — **테스트 재배선의 본보기.** autouse 로그인 fixture를
  두고 익명 경계만 별도 테스트로 뺀 패턴
- `backend/tests/conftest.py` — `login_as`, `upload_document`(내부에서 `login_as`를 부른다),
  `db_client`
- `scripts/demo_recovery.sh` — `status_value`(:85-96), `api_ready`(:113), `jobs_drained`(:121),
  `consistent`(:126), `ensure_demo_session`(:224-235), 호출 순서(:284-306, :366-370)
- `docs/ADR.md` ADR-028 — 인증 경계 결정. **본문 수정은 step 6에서 한다**

## 작업

### 1) 테스트를 먼저 고친다

`backend/tests/test_system_api.py`를 재배선한다.

**(a) 무인증 계약 테스트를 401 계약 테스트로 바꾼다.** `test_diagnostics.py:41` 방식 그대로:

```python
def test_status_requires_login(db_client: TestClient):
    db_client.post("/api/auth/logout")
    assert db_client.get("/api/system/status").status_code == 401
```

**(b) 기존 필드 검증은 로그인 상태로 옮긴다.** 응답 키 집합·값 단언
(`set(body) == {...}`, `assert "reconnect_events" not in body`, 잡 카운터 4종, `node_port` 타입
검사)은 **한 줄도 지우지 마라.** 로그인한 사용자에게는 그대로 나와야 한다.

**(c) autouse 로그인 fixture를 둔다.** `test_diagnostics.py:31`과 같은 형태로:

```python
@pytest.fixture(autouse=True)
def logged_in(db_client: TestClient):
    """시스템 상태 조회도 로그인을 요구한다 (ADR-028). 익명 경계는 아래에서 따로 단언한다."""
    login_as(db_client, "alice")
```

이 파일의 나머지 테스트 4개는 status를 여러 번 부르므로 이 fixture가 없으면 전부 401로
떨어진다. `upload()` 헬퍼가 내부적으로 `login_as`를 부르지만, 업로드 **전에** status를 부르는
테스트가 있어 fixture 쪽이 확실하다.

**이 시점에 실행하면 (a)가 200을 받아 실패한다.** 그게 정상이다.

### 2) 라우터에 인증을 건다

`backend/app/api/system.py`의 `/status` 핸들러에 `require_user_id` 의존성을 추가한다.
`user_id` 값 자체는 쿼리에 쓰이지 않는다 — 운영 지표는 문서 열람 범위와 무관한 전역 카운터다.
값을 쓰지 않는 의존성을 다는 방법은 이 저장소에 이미 두 가지가 있으니(`api/admin.py:9-13`의
라우터 레벨 `dependencies=[...]`, `api/documents.py`의 파라미터 주입) 상황에 맞는 쪽을 골라라.

**잡 카운터·정합성 카운트에 열람 범위 필터를 걸지 마라.** 이것은 문서 목록이 아니라 시스템
지표다. 사용자마다 다른 수를 보여주면 "어긋난 구간이 0으로 돌아오는 것을 증명한다"는 C2의
의미가 사라진다. 인증은 **볼 자격**을 요구하는 것이지 **범위를 나누는** 것이 아니다.

### 3) `demo_recovery.sh`를 재배선한다

**(a) `api_ready`를 `/api/health`로 옮긴다.**

```bash
api_ready() {
  curl -fsS --max-time 2 "$API_URL/api/health" >/dev/null 2>&1
}
```

**주석에 반드시 남길 것**: 왜 status가 아니라 health인가 — *"status는 로그인을 요구하므로
(ADR-028) 세션이 생기기 전인 이 시점에는 쓸 수 없다. health는 토폴로지·문서 정보가 없어
무인증으로 남는다"*.

**(b) `status_value`에 쿠키를 붙인다.** `upload_document`(:240-243)가 쓰는 것과 같은 방식으로
`-b "$COOKIE_JAR"`를 더한다. 이 함수를 부르는 곳은 전부 `ensure_demo_session`(L285) 이후다.

**(c) 순서를 확인한다.** 변경 후 `grep -n "status_value\|api_ready\|ensure_demo_session" scripts/demo_recovery.sh`로
호출 순서를 눈으로 확인하라. `ensure_demo_session`보다 앞선 status 호출이 **하나도 없어야**
한다. 있으면 그 호출을 health로 옮기거나 로그인 뒤로 미뤄라.

**실 VM 없이는 데모를 완주할 수 없다.** AC의 `bash -n` 구문 검사와 위 순서 확인까지가 이
step에서 가능한 검증이다. 완주 검증은 사람이 별도로 한다 — **VM에 접속하려 하지 마라.**

## Acceptance Criteria

```bash
cd backend

# 1) 익명 401 + 로그인 시 기존 필드 전량
.venv/bin/pytest tests/test_system_api.py -q
#   → 전부 passed. 401 테스트와 필드 검증 테스트가 모두 있어야 한다

# 2) 401 계약이 실제로 테스트에 있다
grep -c "status_code == 401" tests/test_system_api.py
#   → 1 이상

# 3) 키 집합 단언이 지워지지 않았다
grep -c "reconnect_events" tests/test_system_api.py
#   → 1 이상 (이 단언이 사라졌으면 (b)를 어긴 것이다)

# 4) 라우터에 인증이 걸렸다
grep -c "require_user_id" app/api/system.py
#   → 1 이상

# 5) 데모 스크립트: 기동 대기가 health를 본다
grep -n "api/health" ../scripts/demo_recovery.sh
#   → api_ready 함수 안에서 1건

# 6) 데모 스크립트: 로그인 이전에 status를 부르는 곳이 없다
grep -n "status_value\|api_ready\|ensure_demo_session" ../scripts/demo_recovery.sh
#   → ensure_demo_session 호출(L285 근처)보다 앞선 줄에 status_value가 없어야 한다.
#      함수 "정의"는 앞쪽에 있어도 무방하다 — 보는 것은 "호출" 순서다

# 7) 셸 구문
bash -n ../scripts/demo_recovery.sh
#   → 출력 없음 (에러 없음)

# 8) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 잡 카운터·정합성 카운트에 열람 범위 필터가 들어가지 않았는가?
   - `/api/health`가 그대로 무인증인가?
   - 응답 필드가 하나도 바뀌지 않았는가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 6에서 일괄 처리).
4. 결과에 따라 `phases/m11a-contract-seal/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`api_ready`에 쿠키를 붙여 해결하려 하지 마라.** 이유: 그 시점에는 세션이 존재하지 않는다.
  로그인은 `api_ready` 다음 줄에서 일어난다.
- **`/api/health`에 인증을 걸지 마라.** 이유: 기동 대기의 유일한 무인증 창구가 사라진다.
- **`/api/health`에 DB 상태나 노드 정보를 추가하지 마라.** 이유: 그러면 health도 인증이 필요한
  표면이 되어 같은 문제가 반복된다.
- **잡 카운터에 사용자별 필터를 걸지 마라.** 이유: 시스템 지표이지 문서 목록이 아니다.
  사용자마다 다른 수를 보이면 C2의 증명 수단이 무너진다.
- **응답 키 집합 단언(`set(body) == {...}`)이나 `reconnect_events` 단언을 지우지 마라.**
  이유: 401만 바뀌었을 뿐 응답 계약은 그대로임을 이 단언들이 보증한다.
- **VM이나 EC2에 접속하려 하지 마라.** 이유: 실 환경 완주 검증은 사람의 몫이고, 이 step은
  구문 검사와 호출 순서 확인까지가 범위다.
- **프론트엔드를 고치지 마라.** 이유: `/admin/status` 화면 대응은 step 3이다.
- **기존 테스트를 깨뜨리지 마라.**
