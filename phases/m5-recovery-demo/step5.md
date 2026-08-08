# Step 5: docs-sync

## 배경

이 phase에서 코드와 스크립트가 세 가지 바뀌었다. 문서를 현재 사실에 맞춘다.

1. **좀비 회수 임계가 설정으로 빠졌다** — `zombie_timeout_minutes`(기본 5)
2. **`reconnect_events` 응답 필드와 운영 화면 카드가 제거됐다** — 수집하지 않기로 결정했다
3. **`scripts/demo_recovery.sh`가 생겼다** — DB 정지·워커 강제 종료로부터의 복구를 검증한다

문서가 코드보다 앞서 나가 있으면(구현하지 않은 것을 구현한 것처럼 적혀 있으면) 심사는 저장소
상태를 기준으로 하므로 그대로 감점 요인이 된다.

## 읽어야 할 파일

- `phases/m5-recovery-demo/step1.md` ~ `step4.md` — 이 phase에서 무엇이 왜 바뀌었는지
- `docs/ARCHITECTURE.md` — **377행 근처** API 표의 `GET /api/system/status` 행,
  **379행 근처**의 `> **구현 현황 (M4 기준)**` 각주, **256행 근처**의 좀비 회수 설명
- `docs/UI_GUIDE.md` — **146행 근처**의 운영 화면 표시 항목 목록
- `README.md` — "빠른 시작" 절과 "검증" 절
- `.env.example` — step 1이 `ZOMBIE_TIMEOUT_MINUTES`를 추가했는지 확인. 없으면 여기서 추가한다
- `scripts/demo_recovery.sh` — 실제로 만들어진 스크립트의 사용법과 환경변수

## 작업

### 1) `docs/ARCHITECTURE.md`

- **API 표의 `GET /api/system/status` 행**에서 `최근 재연결 이벤트` 항목을 제거한다.
  나머지 항목(`inet_server_addr()`, 잡 수, 프로바이더명, 정합성 검증 쿼리 결과)은 그대로 둔다
- **`> **구현 현황 (M4 기준)**` 각주**를 갱신한다. 현재는 *"남은 것은 M5의 `reconnect_events`
  값 채우기다"*라고 적혀 있는데, **채우지 않고 제거했다**는 것이 사실이다. 왜 제거했는지
  한 줄로 남겨라 — 복구 데모가 워커 로그와 잡·정합성 카운터로 같은 사실을 증명하기 때문이다
- **좀비 회수 설명(256행 근처)**의 "`processing` 상태로 **5분** 초과된 잡"에 그 값이 이제
  설정(`ZOMBIE_TIMEOUT_MINUTES`, 기본 5)이라는 것을 반영한다
- **복구 데모 절차**를 짧게 추가한다. 어디에 둘지는 판단에 맡기되, "애플리케이션이 담당하는
  복구 로직" 절 근처가 자연스럽다. 다음을 포함하라:
  - 실행 방법과 두 환경(로컬 컨테이너 / 실 OpenSQL VM) 전환 방법(`DB_STOP_CMD`·`DB_START_CMD`)
  - 이 데모가 증명하는 것과 **증명하지 않는 것**(Patroni 승격). ADR-020 결정 3의 표를 참조하되
    통째로 복사하지는 마라

### 2) `docs/UI_GUIDE.md`

운영 화면 표시 항목에서 `최근 재연결 이벤트 로그`를 제거한다. 다른 항목은 건드리지 않는다.

### 3) `README.md`

- **"검증" 절**에 복구 데모 실행을 추가한다. 전제 조건(로컬 DB 기동, `backend/.venv` 준비)과
  실행 커맨드, 그리고 실 OpenSQL로 돌릴 때의 환경변수 주입 예시를 적는다
- **"무엇을 보장하고, 무엇을 보장하지 않는가" 절**이 이미 있다. 복구 데모가 그 보장 범위를
  실제로 검증한다는 연결을 한 문장으로 넣어라. **새 절을 만들지 말고 기존 절에 녹여라**

### 4) `.env.example`

step 1이 `ZOMBIE_TIMEOUT_MINUTES`를 추가하지 않았다면 여기서 추가한다. 이미 있으면 건드리지 않는다.

## Acceptance Criteria

```bash
# 1) 제거된 필드가 문서에서도 사라졌는지 (출력 없어야 함)
grep -rn "reconnect_events" docs/ README.md
grep -n "재연결 이벤트" docs/ARCHITECTURE.md docs/UI_GUIDE.md

# 2) 데모 스크립트가 문서에 등장하는지
grep -rn "demo_recovery" README.md docs/ARCHITECTURE.md

# 3) 좀비 임계 설정이 문서화됐는지
grep -rn "ZOMBIE_TIMEOUT_MINUTES" .env.example docs/ARCHITECTURE.md

# 4) 금지 문구가 사용자 대상 문서에 새로 들어가지 않았는지 (출력 없어야 함)
grep -rnE "failover를 시연|무중단|항상 최신|실시간 동기화" README.md docs/UI_GUIDE.md

# 4-1) ARCHITECTURE.md는 이 금지어를 규칙으로 인용하는 문서다. 인용까지 막으면 규칙 문장이
#      지워지므로 자동 판정하지 않고 눈으로 본다 — 보장으로 쓰였으면 위반, "쓰지 않는다"의
#      목적어로 쓰였으면 정상이다.
grep -rnE "무중단|항상 최신|실시간 동기화" docs/ARCHITECTURE.md

# 5) 코드를 건드리지 않았는지 — 출력 없어야 함
git diff --name-only | grep -E "\.(py|ts|tsx|sql|sh)$"

# 6) 전체 검증
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 문서가 실제 코드 상태와 일치하는가? (구현하지 않은 것을 구현한 것처럼 적지 않았는가)
   - ADR-020 결정 4의 표현 규칙을 지켰는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m5-recovery-demo/index.json`의 step 5를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **"재연결"이라는 단어가 들어간 모든 문장을 지우지 마라.** 이유: 지워야 할 것은
  **`reconnect_events` 필드와 그 UI 항목에 대한 서술 두 곳**뿐이다. 아래는 전부 실제로 일어나는
  동작에 대한 서술이며 **반드시 남긴다**:
  - `docs/ARCHITECTURE.md` 상단 구성도의 `커넥션 풀링 · Primary 추적 · 재연결` (OpenProxy 설명)
  - `docs/ARCHITECTURE.md` 책임 분리표의 `Primary 변경 감지 후 재연결, 커넥션 풀링, VIP 이중화 | **OpenProxy**`
  - `docs/ARCHITECTURE.md` "애플리케이션이 담당하는 복구 로직"의 워커 백오프 재연결·LISTEN 재등록 서술
  - `docs/ARCHITECTURE.md` 잡 큐 내구성 절의 "워커 재연결 즉시 재개된다"
  - `docs/PRD.md`의 페일오버 자동 복구 항목
- **`docs/ADR.md`를 수정하지 마라.** 이유: 이 phase의 ADR 변경은 step 0이 이미 했다(보강 4).
  새 설계 결정이 필요하다고 판단되면 고치지 말고 summary에 적어라
- **`docs/PROJECT_CONTEXT.md`와 `docs/OPENSQL_RESEARCH.md`를 수정하지 마라.** 이유: 전자는 대회
  요건 기록이고 후자는 실측 기록이다. 이 phase는 둘 중 어느 것도 바꾸지 않았다
- **코드 파일을 수정하지 마라** (`.py`, `.ts`, `.tsx`, `.sql`, `.sh`). 이유: 문서 동기화 step이다.
  문서와 코드가 어긋난 곳을 발견하면 코드를 고치지 말고 summary에 적어라
- **README에 새 절을 만들지 마라.** 이유: 기존 구조가 이미 정돈되어 있고, 절이 늘어날수록
  clean clone 검증에서 따라가야 할 절차가 흩어진다
- 기존 테스트를 깨뜨리지 마라
