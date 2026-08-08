# Step 2: drop-reconnect-field

## 배경 — 왜 채우지 않고 지우는가

M2 설계 당시 `GET /api/system/status` 응답에 `reconnect_events` 필드를 **자리만 잡아두고**
값 채우기를 M5로 미뤘다. 지금 `backend/app/api/system.py`에 이렇게 남아 있다:

```python
reconnect_events: None = None
```

M5 설계에서 **이 필드를 채우지 않고 제거하기로 결정했다.** 이유:

- 재연결 이벤트를 API·워커 양쪽에서 모으려면 테이블과 마이그레이션이 필요한데, 복구 데모(step 4)가
  워커 로그와 잡 카운터·정합성 카운터로 같은 사실을 이미 증명한다
- 채우지 않은 채 두면 응답 스키마와 운영 화면이 **영원히 `null`인 칸**을 광고하게 된다.
  하지 않은 것을 한 것처럼 보이게 하지 않는다는 것이 이 프로젝트의 태도다(ADR-015·ADR-020 결정 4)

이 step은 **백엔드만** 정리한다. 프론트엔드는 step 3에서 따로 한다 — 커밋 스코프가 다르기 때문이다.

## 읽어야 할 파일

- `backend/app/api/system.py` — `SystemStatus` 모델(19~27행 근처)과 그 docstring
- `backend/tests/test_system_api.py` — **24행과 29행 근처**에서 이 필드의 존재와 `None` 값을 단언한다
- `docs/ARCHITECTURE.md` — 377행 근처 API 표에 "최근 재연결 이벤트"가 적혀 있다.
  **이 step에서는 문서를 고치지 마라. step 5가 한다**

## 작업

### 1) 테스트를 먼저 고친다

`backend/tests/test_system_api.py`에서 `reconnect_events`에 대한 단언을 제거한다:

- 응답 키 목록 검증(24행 근처)에서 `"reconnect_events"` 항목을 뺀다
- `assert body["reconnect_events"] is None`(29행 근처)을 삭제한다

**대신 "해당 키가 응답에 없다"를 단언하는 줄을 추가하라.** 이유: 단순 삭제만 하면 필드가 남아
있어도 테스트가 통과한다. 제거를 실제로 강제하는 단언이 있어야 한다.

### 2) `backend/app/api/system.py`를 고친다

- `SystemStatus`에서 `reconnect_events: None = None` 필드를 제거한다
- 클래스 docstring의 `"""운영 상태 응답. reconnect_events는 M5에서 재연결 추적을 구현할 때 채운다."""`를
  현재 사실에 맞게 고친다. **"M5에서 채운다"는 서술은 이제 거짓이므로 남기면 안 된다**

`get_system_status` 함수의 SQL과 반환 로직은 이 필드를 쓰지 않으므로 건드릴 것이 없다.
건드리지 마라.

## Acceptance Criteria

```bash
cd backend

# 1) 백엔드 구현부에서 필드가 사라졌는지
grep -rn "reconnect_events" app/            # 출력 없어야 함
# tests/는 검색 범위에서 뺀다 — "필드가 없다"를 검증하려면 테스트가 그 이름을 써야 한다.
# 여기에 tests/를 넣으면 테스트가 문자열을 쪼개 grep을 피해가고, 검사는 초록인데 잔재는 남는다.

# 2) 시스템 API 테스트
.venv/bin/pytest tests/test_system_api.py -v

# 3) 전체 검증
cd .. && bash scripts/check.sh
```

`scripts/check.sh`의 frontend 단계도 통과해야 한다. 프론트의 `types.ts`에는 아직
`reconnect_events`가 남아 있지만, 그 타입은 자기 완결적이라 백엔드 변경만으로는 빌드가 깨지지 않는다.
**깨진다면 step 3의 범위를 침범한 것이므로 되돌려라.**

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `frontend/` 아래 파일을 하나도 건드리지 않았는가?
   - `docs/` 아래 파일을 하나도 건드리지 않았는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m5-recovery-demo/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **"재연결"이라는 단어가 들어간 다른 코드·주석을 지우지 마라.** 이유: `grep -rn "재연결"`은
  저장소에서 9곳 넘게 걸리는데, **지워야 할 것은 `reconnect_events` 필드 하나**다. 나머지는 전부
  실제로 일어나는 동작에 대한 서술이며 지우면 설계 근거가 사라진다. 특히:
  - `backend/app/config.py`의 `# 재연결은 OpenProxy의 책임이다 (ADR-006).` — **남긴다**
  - `backend/app/worker.py`의 LISTEN 백오프 재연결 관련 주석·로그 — **남긴다**
- **`frontend/` 아래를 건드리지 마라.** 이유: step 3의 범위다. 커밋 스코프가 `api`와 `frontend`로
  갈리므로 한 커밋에 섞으면 CLAUDE.md의 커밋 규칙을 어긴다
- **`docs/` 아래를 건드리지 마라.** 이유: step 5가 문서를 일괄 동기화한다
- **재연결 추적 기능을 새로 구현하지 마라.** 이유: 이 step은 제거다. 마이그레이션 파일이나
  이벤트 테이블을 만들면 M5 설계 결정과 정면으로 어긋난다
- 기존 테스트를 깨뜨리지 마라
