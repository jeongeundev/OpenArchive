# Step 3: status-panel-edges

상태 화면이 관계 미반영 문서 수를 함께 보여준다. 카드를 새로 만들지 않고 **정합성 검증 카드 안**에 둔다 —
둘 다 "원본과 파생 데이터가 아직 어긋나 있는 구간"을 세는 같은 성격의 지표다.

## 읽어야 할 파일

- `/docs/UI_GUIDE.md` — 디자인 원칙, 화면 문구 규칙(「쓸 말 / 쓰지 말 것」 표), 상태 표현
- `/docs/ADR.md` — ADR-015(보장 범위 문구 — "항상 최신"·"실시간 동기화" 금지) · ADR-041(동봉 정적 빌드)
- `/CONTRIBUTING.md` — 프론트 소스를 고쳤으면 `npm run build:static`으로 `backend/app/static/`을 갱신해 함께 커밋
- `frontend/src/lib/types.ts` — `SystemStatus`(121행)
- `frontend/src/components/StatusPanel.tsx` — 정합성 검증 카드(12~16행)
- `frontend/src/components/StatusPanel.test.tsx` — 기존 테스트의 픽스처 모양을 그대로 따라라
- `frontend/src/lib/useSystemStatus.test.ts` — 응답 픽스처가 여기에도 있다
- `backend/app/api/schemas.py` — **step 2 산출물**. 응답 필드명(`stale_edge_documents`)의 정본

## 작업

### 1) 테스트 먼저 — `frontend/src/components/StatusPanel.test.tsx`

1. `stale_edge_documents`가 0이면 정합성 카드에 "관계까지 반영됨"에 해당하는 표시가 나오고,
   0보다 크면 그 수가 보인다. `data-testid="stale-edge-count"`로 집어라(기존 `consistency-count`와 같은 방식).
2. 값이 0일 때와 0보다 클 때 **색이 다르다**(기존 `inconsistent_documents`가 `#22c55e`/`#a3a3a3`로
   가르는 것과 같은 규칙을 따라라).
3. 기존 테스트 픽스처에 새 필드를 더해도 **다른 단언이 깨지지 않는다**.

`frontend/src/lib/useSystemStatus.test.ts`의 픽스처에도 필드를 더한다(타입 에러 제거).

### 2) 구현

- `frontend/src/lib/types.ts` — `SystemStatus`에 `stale_edge_documents: number`.
- `frontend/src/components/StatusPanel.tsx` — 정합성 검증 카드 안에 둘째 지표를 둔다.
  문구 예시(그대로 쓰지 않아도 되나 의미는 지켜라):
  「관계 미반영 문서 N건 — 임베딩이 끝난 뒤 관계 계산이 따로 처리됩니다. 잡이 끝나면 0으로 돌아옵니다.」
  **"실시간"·"항상 최신"·"즉시"를 쓰지 마라** (ADR-015). 두 수가 한 카드에 있으므로 각각 무엇을 세는지
  레이블로 구분하라 — 하나는 「원본과 청크 버전」, 다른 하나는 「관계」다.
- 카드 레이아웃은 기존 Tailwind 클래스 관용을 따른다. 새 색·새 간격 토큰을 도입하지 마라.

### 3) 동봉 빌드

```bash
cd frontend && npm run build:static
```

`backend/app/static/` 변경분을 **함께 남겨라**(커밋은 execute.py가 한다). 이것을 빼면 `check.sh`가
소스와 동봉 빌드의 불일치로 실패한다.

## Acceptance Criteria

```bash
cd frontend && npx tsc --noEmit -p .
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build:static
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 전부 실행한다. `check.sh`가 초록이어야 한다.
2. 아키텍처·UI 체크리스트:
   - UI_GUIDE의 문구 규칙을 지켰는가? 금지 표현("항상 최신"·"실시간 동기화")이 없는가?
   - 사용자 화면(`/`·`/search`·`/documents/*`)에 인프라 상태를 노출하지 않았는가? — 이 화면은 관리 화면이다
   - 동봉 정적 빌드를 갱신했는가? (ADR-041)
3. `phases/m15-edge-jobs/index.json`의 step 3을 갱신한다(성공/error/blocked 규칙은 앞 step과 같다).

## 금지사항

- **새 카드·새 화면을 만들지 마라.** 이유: 지표 하나가 느는 것이고, 관리 화면의 정보 밀도를
  올리는 것은 이 이슈의 범위가 아니다.
- **폴링 주기·자동 새로고침 동작을 바꾸지 마라.** 이유: `useSystemStatus`의 주기는 별도 결정이다.
- **관계 미반영 건수를 경고·에러 색(붉은색)으로 칠하지 마라.** 이유: 그것은 정상 처리 중인 상태이고,
  붉은색은 `jobs.error`가 쓴다.
- 기존 테스트를 깨뜨리지 마라.
