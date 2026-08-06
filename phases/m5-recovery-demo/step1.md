# Step 1: zombie-timeout-config

## 배경 — 왜 이 값을 설정으로 빼는가

M5의 복구 데모(step 4)는 **좀비 잡 회수**를 시연한다. 워커가 잡을 `processing`으로 집어간 상태에서
강제 종료되면 그 잡은 얼어붙고, 스윕이 그것을 `pending`으로 되돌린다.

그런데 회수 임계가 `backend/app/worker.py`의 **모듈 상수 `ZOMBIE_TIMEOUT_MINUTES = 5`**다.
데모에서 5분을 기다릴 수 없다. 이 값을 실행 시점에 주입할 수 있어야 한다.

## 읽어야 할 파일

- `backend/app/worker.py` — 상수 정의부(38~40행 근처), `sweep_zombies`(228행 근처),
  `run_worker`(352행 근처). 특히 **`run_worker`의 루프가 `sweep → drain → 대기`를 순차로 돈다**는
  점을 확인하라. 이 순차성 때문에 임계를 0으로 낮춰도 워커가 자기 잡을 좀비로 오인하지 않는다
- `backend/app/config.py` — `Settings` 클래스. 여기에 필드를 추가한다
- `backend/tests/test_worker.py` — **12번째 줄 근처의 모듈 docstring**과 483·503·526행 근처의
  좀비 테스트 3개. 기존 테스트는 `started_at`을 `now() - interval '10 minutes'`로 직접 밀어
  5분 임계를 넘긴다. **기본값을 5로 유지하면 이 테스트들은 그대로 통과한다**
- `backend/tests/conftest.py` — `_clear_settings_cache` autouse 픽스처. `get_settings()`가
  `lru_cache`라서 환경변수를 바꾸는 테스트가 서로 오염되지 않도록 매 테스트마다 캐시를 비운다.
  이 step의 새 테스트가 이 픽스처에 의존한다
- `.env.example`

## 작업

### 1) 테스트를 먼저 작성한다

`backend/tests/test_worker.py`에 아래 세 가지를 검증하는 테스트를 추가하라.
**구현보다 먼저 쓰고, 실패하는 것을 확인한 뒤 구현으로 넘어간다.**

1. **임계를 0으로 주면 방금 시작한 `processing` 잡도 회수된다.**
   `started_at`을 과거로 밀지 **않은** 잡이 회수되어야 한다. 이것이 데모가 의존하는 동작이다
2. **기본 설정에서는 방금 시작한 잡이 회수되지 않는다.** 회수 건수가 0이어야 한다
3. **명시적 인자가 설정값을 이긴다.** 환경 설정과 다른 값을 인자로 넘겼을 때 인자가 적용된다

환경변수 기반 검증에는 `monkeypatch.setenv("ZOMBIE_TIMEOUT_MINUTES", "0")`를 쓴다.
`_clear_settings_cache`가 autouse이므로 캐시 정리를 따로 하지 않아도 된다 — 다만 픽스처가
테스트 **시작 전**에 비우므로, `monkeypatch.setenv`는 `get_settings()`를 처음 호출하기 전에 해야 한다.

### 2) `backend/app/config.py`에 설정을 추가한다

```python
zombie_timeout_minutes: int = 5
```

주석으로 다음을 남겨라: **0은 데모 전용 값이며, 워커가 여러 대인 환경에서 0을 쓰면 정상 처리 중인
잡을 서로 회수한다.** 데모는 워커 1대이고 `run_worker`의 루프가 순차라서 안전하다.

### 3) `backend/app/worker.py`를 고친다

- 모듈 상수 `ZOMBIE_TIMEOUT_MINUTES = 5`를 **제거**한다
- `sweep_zombies`의 시그니처를 다음으로 바꾼다:

```python
async def sweep_zombies(
    conn: psycopg.AsyncConnection, *, timeout_minutes: int | None = None
) -> int: ...
```

- `timeout_minutes`가 `None`이면 **함수 안에서** `get_settings().zombie_timeout_minutes`를 읽는다
- 함수 본문의 SQL 3곳에 쓰이는 `make_interval(mins => %s)` 파라미터를 이 값으로 바꾼다

**`get_settings()`를 모듈 최상단에서 호출하지 마라.** 이유: `app/db.py`의 모듈 docstring이
정한 규약대로 이 패키지는 import 시 부작용이 없어야 하고, 모듈 로드 시점에 값을 고정하면
테스트가 `monkeypatch.setenv`로 바꾼 값을 볼 수 없다.

### 4) `.env.example`에 항목을 추가한다

기존 항목들과 같은 형식으로, 기본값 5와 "데모에서만 0으로 낮춘다"는 설명을 단다.

## Acceptance Criteria

```bash
cd backend

# 1) 상수가 사라지고 설정으로 옮겨졌는지
grep -rn "ZOMBIE_TIMEOUT_MINUTES" app/ | grep -v "zombie_timeout_minutes"   # 출력 없어야 함
grep -n "zombie_timeout_minutes" app/config.py app/worker.py

# 2) 전체 테스트 — 기존 좀비 테스트 3개가 그대로 통과해야 한다
.venv/bin/pytest tests/test_worker.py -v

# 3) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `get_settings()`가 모듈 최상단이 아니라 함수 안에서 호출되는가?
   - 기본값이 5로 유지되어 기존 동작이 바뀌지 않았는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m5-recovery-demo/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **기존 좀비 테스트 3개(`test_worker.py` 483·503·526행 근처)를 수정하지 마라.** 이유: 기본값을
  5로 유지하면 그 테스트들은 손대지 않아도 통과한다. 통과시키려고 기존 테스트를 고치는 것은
  이 프로젝트가 금지하는 패턴이다
- **`POLL_INTERVAL_SECONDS`와 `MAX_ATTEMPTS`를 건드리지 마라.** 이유: 요청 범위 밖이다.
  데모가 필요로 하는 것은 좀비 임계 하나뿐이다
- **`sweep_zombies`의 SQL 로직(문서 행 `FOR UPDATE` 잠금, 두 개의 `UPDATE` 분기)을 바꾸지 마라.**
  이유: 그 구조는 `uq_pending_job_per_doc` 제약과 코얼레싱이 만나는 지점을 다루는 것이고,
  주석에 적힌 대로 잘못 건드리면 워커가 통째로 죽는다. 이 step은 **임계값의 출처만** 바꾼다
- **`attempts` 초기화를 추가하지 마라.** 이유: 함수 docstring이 설명하듯 초기화하면 계속 죽는 잡이
  영원히 재시도되어 `MAX_ATTEMPTS`가 무의미해진다
- 기존 테스트를 깨뜨리지 마라
