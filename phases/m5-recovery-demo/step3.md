# Step 3: drop-reconnect-panel

## 배경

직전 step에서 백엔드 `GET /api/system/status` 응답의 `reconnect_events` 필드를 제거했다.
프론트엔드에는 그 필드를 받는 타입과, **"아직 수집하지 않습니다"만 표시하는 빈 카드**가 남아 있다.

M5 설계에서 재연결 이벤트 수집을 구현하지 않기로 결정했으므로, 이 카드는 채워질 일이 없다.
운영 화면에서 제거한다. 복구 데모(step 4)가 워커 로그와 잡 카운터로 같은 사실을 증명한다.

## 읽어야 할 파일

- `frontend/src/lib/types.ts` — **88행 근처**의 `SystemStatus` 타입에 `reconnect_events: null;`이 있다
- `frontend/src/components/StatusPanel.tsx` — **26행**이 제거 대상 카드다. 파일 전체가 31줄이니
  전부 읽고 grid 레이아웃을 파악하라
- `frontend/src/components/StatusPanel.test.tsx` — **9행 근처** 픽스처에 필드가 있다
- `frontend/src/lib/useSystemStatus.test.ts` — **13행 근처** 픽스처에 필드가 있다
- `frontend/AGENTS.md` — 이 저장소의 Next.js 버전 관련 주의사항

## 작업

### 1) 테스트 픽스처를 먼저 고친다

`StatusPanel.test.tsx`와 `useSystemStatus.test.ts`의 `SystemStatus` 픽스처에서
`reconnect_events` 항목을 제거한다.

`StatusPanel.test.tsx`에는 **"재연결 이벤트" 문구가 렌더 결과에 없다**를 단언하는 테스트를 추가하라.
이유: 픽스처에서 필드만 빼면 카드가 남아 있어도 통과한다(카드가 `status` 값을 쓰지 않고 고정
문구만 표시하기 때문이다). 제거를 실제로 강제하는 단언이 필요하다.

### 2) `frontend/src/lib/types.ts`에서 필드를 제거한다

`SystemStatus` 타입에서 `reconnect_events: null;` 줄을 삭제한다.

### 3) `frontend/src/components/StatusPanel.tsx`에서 카드를 제거한다

26행의 `<div>` 블록 하나를 통째로 지운다. 다른 카드는 건드리지 않는다.

**레이아웃 확인**: 제거 후 grid에는 정합성 카드(`md:col-span-2`) + 접속 노드 + 임베딩 잡 +
임베딩 프로바이더가 남는다. 2열 grid에서 자연스럽게 배치되므로 **다른 카드의 클래스를 조정하지
마라.** 배치가 실제로 깨졌을 때만 최소한으로 고치고, 무엇을 왜 고쳤는지 summary에 적어라.

## Acceptance Criteria

```bash
cd frontend

# 1) 필드와 카드가 사라졌는지 — 구현부만 본다. 출력 없어야 함
# 테스트 파일은 제외한다: "카드가 없다"를 검증하려면 테스트가 그 문구를 써야 하는데,
# 여기서 걸리게 두면 테스트가 문자열을 쪼개 grep을 피해간다(검사는 초록, 잔재는 잔존).
grep -rn "reconnect_events" src/ | grep -v "\.test\."
grep -rn "재연결" src/ | grep -v "\.test\."

# 2) 프론트 검증
npm run lint
npm run test
npm run build

# 3) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `UI_GUIDE.md`의 화면 규칙(다크 배경, 슬롭 패턴 금지)을 유지했는가?
   - 제거 외에 다른 변경(리팩터링, 스타일 개선)을 하지 않았는가?
   - `backend/`와 `docs/`를 건드리지 않았는가?
3. 결과에 따라 `phases/m5-recovery-demo/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **남은 카드들의 마크업·클래스를 "정리"하지 마라.** 이유: 이 step의 diff는 카드 하나 제거와
  타입 한 줄 제거로 끝나야 한다. 인접 코드를 개선하는 것은 이 프로젝트가 금지하는 패턴이다
- **`backend/`와 `docs/`를 건드리지 마라.** 이유: 백엔드는 step 2가 끝냈고 문서는 step 5가 한다
- **슬롭 패턴을 넣지 마라** — `backdrop-blur`, `bg-gradient`, `purple-*`, `animate-pulse` 등.
  이유: M3부터 AC에 grep 검사가 걸려 있다
- **재연결 이벤트 UI를 다른 형태로 되살리지 마라.** 이유: 수집하지 않는 데이터를 화면에
  광고하지 않는다는 것이 제거의 이유다
- 기존 테스트를 깨뜨리지 마라
