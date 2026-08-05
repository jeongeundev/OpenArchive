# Step 2: document-list

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/UI_GUIDE.md` — 화면 구성 1번(`/` 목록), 색상표, 컴포넌트 클래스, 용어 규칙, AI 슬롭 안티패턴
- `/docs/ARCHITECTURE.md` — "프론트엔드 패턴"·"상태 관리" 절
- `/docs/PRD.md` — 보장 범위(버전 일관성 + 최신 수렴). "항상 최신"·"실시간 동기화"라고 쓰지 않는다
- `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/lib/user.ts` — step 0 산출물
- `frontend/src/components/StatusBadge.tsx`, `SiteHeader.tsx` — step 1 산출물
- `frontend/src/app/layout.tsx`, `frontend/src/app/page.tsx`, `frontend/src/__tests__/page.test.tsx` — 현재 상태
- `frontend/node_modules/next/dist/docs/01-app/` — Next 16.3.0 App Router 규약(학습 데이터와 다를 수 있다)

## 작업

`/` 화면의 **문서 목록과 상태 폴링**을 만든다. 업로드 드롭존은 step 3에서 붙이므로 여기서는 만들지 않는다.

### 1. `frontend/src/lib/useDocuments.ts` + `useDocuments.test.ts`

```ts
export function useDocuments(params?: {
  status?: EmbeddingStatus;
  intervalMs?: number;   // 기본 2000
}): {
  documents: DocumentSummary[];
  loading: boolean;      // 첫 로드 중에만 true
  error: string | null;
  refresh: () => void;   // 즉시 재조회
};
```

- 마운트 직후 1회 조회하고, 이후 `intervalMs`마다 재조회한다(기본 2초 — ARCHITECTURE "상태 관리")
- **언마운트 시 타이머를 반드시 정리한다.** 정리하지 않으면 테스트가 끝난 뒤에도 fetch가 돌고 다른 테스트를 오염시킨다
- **폴링 중 실패해도 마지막 성공 데이터를 지우지 마라.** `error`만 채우고 `documents`는 유지한다. 이유: 페일오버로 요청이 잠깐 실패해도 사용자는 "업로드와 검색이 계속 동작한다"는 결과만 보아야 한다(UI_GUIDE 디자인 원칙 3). 화면이 빈 목록으로 깜빡이면 그 원칙이 깨진다
- 요청이 겹치지 않게 한다 — 이전 요청이 끝나기 전에 다음 주기가 오면 건너뛴다
- SSE·웹소켓을 쓰지 마라(PRD MVP 제외 사항). 폴링이 정해진 방식이다

테스트에서는 `vi.useFakeTimers()`와 `vi.stubGlobal("fetch", ...)`를 쓴다. 최소한 검증할 것:
- 마운트 시 1회 호출된다
- 2초 경과 후 다시 호출된다
- 언마운트 후에는 더 호출되지 않는다
- 두 번째 호출이 실패해도 첫 번째 결과가 그대로 남고 `error`가 채워진다

### 2. `frontend/src/components/DocumentTable.tsx` + `DocumentTable.test.tsx`

```tsx
export function DocumentTable({ documents }: { documents: DocumentSummary[] }): React.ReactElement;
```

- 열: **제목**(`/documents/{id}` 링크) · 유형 · 태그 · 공개범위 · 상태 배지 · 수정일
- 제목 링크는 **문서 상세로 간다.** 원본 파일 링크를 만들지 마라 — 원본 파일은 보관하지 않는다(UI_GUIDE 용어 규칙)
- 상태 열은 step 1의 `<StatusBadge />`를 재사용한다. 배지를 다시 구현하지 마라
- `visibility`는 "공개"/"비공개"로 표기한다
- 날짜는 한국 로케일로 읽기 쉽게 표시한다(초 단위까지 필요 없다)
- 빈 목록이면 행 대신 안내 한 줄: "아직 문서가 없습니다." — `text-neutral-500`. **오류처럼 보이게 하지 마라**(빨간색·경고 아이콘 금지)
- 데이터가 주인공이다(UI_GUIDE 디자인 원칙 2). 행마다 카드를 겹치지 말고 테이블로 조밀하게 보여라

테스트: 제목이 상세 링크로 렌더된다 / 상태 배지가 문서 수만큼 나온다 / 빈 배열이면 안내 문구가 나온다.

### 3. `frontend/src/app/page.tsx` 교체

- `"use client"` — 폴링과 사용자별 조회가 브라우저 상태에 의존한다
- `useDocuments()`로 목록을 얻어 `<DocumentTable />`에 넘긴다
- 페이지 제목(`text-4xl font-semibold text-white`, UI_GUIDE 타이포그래피)과 짧은 설명 한 줄
- **`page.tsx`에는 조립만 둔다.** 데이터 페칭 로직·상태 계산·행 렌더링을 여기에 인라인하지 마라. 이유: `scripts/hooks/tdd-guard.sh`가 `page.tsx`를 테스트 없이 통과시키므로, 로직이 여기 들어가면 검증되지 않은 코드가 남는다
- 첫 로드 중에는 짧은 안내("불러오는 중…") 정도만 둔다. **스켈레톤 애니메이션을 넣지 마라**(`animate-pulse` 금지 — UI_GUIDE 애니메이션 절)

### 4. `frontend/src/__tests__/page.test.tsx` 갱신

기존 테스트는 스캐폴드의 "구현 진행 중입니다" 화면을 검증하므로 이 step에서 깨진다. **삭제하지 말고 새 화면을 검증하도록 고쳐라** — 예: `fetch`를 스텁해 문서 2건을 반환시키고, 제목이 링크로 렌더되는지 확인한다.

## Acceptance Criteria

```bash
cd frontend
npm run lint
npm run test
npm run build
```

UI 슬롭 안티패턴 검사(매치가 있으면 실패):

```bash
cd frontend && ! grep -rEn "backdrop-blur|backdrop-filter|bg-gradient|blur-3xl|purple-|indigo-|violet-|animate-(pulse|bounce|spin|ping)" src/
```

과장된 문구 검사(매치가 있으면 실패 — ADR-015):

```bash
cd frontend && ! grep -rn "항상 최신\|실시간 동기화\|무중단" src/
```

## 검증 절차

1. 위 AC 커맨드를 전부 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 폴링 주기가 2초이고 언마운트 시 정리되는가?
   - 폴링 실패가 화면을 비우지 않는가?
   - 제목 링크가 문서 상세로 가는가? (원본 파일 링크가 아니다)
   - `page.tsx`에 로직이 남아 있지 않은가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m3-frontend/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (훅 시그니처와 `page.tsx` 구성 방식을 담아라 — step 3이 같은 페이지에 드롭존을 붙인다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **업로드 UI를 만들지 마라.** step 3의 범위다. 이유: 같은 파일을 두 step이 동시에 설계하면 충돌한다
- **필터·정렬·페이지네이션을 추가하지 마라.** 이유: 요청받지 않은 기능이다. `useDocuments`의 `status` 파라미터는 step 7(운영 화면)이 `error` 문서를 뽑는 데 쓸 것이므로 시그니처에만 두고 화면에 필터 UI를 만들지 않는다
- **SSE·웹소켓·react-query 등을 도입하지 마라.** 이유: 폴링이 정해진 방식이고(PRD MVP 제외 사항, ARCHITECTURE 상태 관리), 전역 상태 라이브러리를 쓰지 않는다
- **"항상 최신", "실시간 동기화", "무중단"이라고 쓰지 마라.** 이유: 보장 범위는 버전 일관성과 최신 수렴이다(ADR-015). 워커 주기와 임베딩 시간만큼 반영이 늦는다
- **접속 노드·잡 수 같은 운영 정보를 이 화면에 넣지 마라.** 이유: 사용자 화면은 인프라를 드러내지 않는다(UI_GUIDE 디자인 원칙 3)
- 기존 테스트를 깨뜨리지 마라 (`src/__tests__/page.test.tsx`는 갱신 대상이지 삭제 대상이 아니다)
