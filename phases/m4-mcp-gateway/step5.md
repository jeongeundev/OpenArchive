# Step 5: related-panel

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 UI 규칙과 기존 패턴을 파악하라:

- `/docs/UI_GUIDE.md` — **"관련 문서 · 태그 추천"** 절과 **"유사 문서 표시 — '중복'이라고 쓰지 않는다"** 절
- `/docs/ADR.md` — **ADR-018**의 "점수의 정확한 의미"(비대칭 지표), **ADR-015**(문구 규칙)
- `frontend/src/app/documents/[id]/page.tsx` — **51~61행에 하드코딩된 자리표시**가 있다. 이 step이 교체할 대상이다
- `frontend/src/lib/api.ts` — `request()` 헬퍼와 기존 API 함수들
- `frontend/src/lib/types.ts` — 백엔드 계약 타입
- `frontend/src/lib/useDocument.ts` — 조건부 폴링 훅의 구조(`mountedRef`/`inFlightRef` 패턴)
- `frontend/src/components/VersionHistory.tsx`, `DocumentMeta.tsx` — 섹션 컴포넌트의 마크업·스타일 관례
- `frontend/src/components/DocumentTable.test.tsx` — 컴포넌트 테스트 관례(Vitest + Testing Library)
- `phases/m4-mcp-gateway/index.json`의 step 2 summary — 백엔드가 실제로 반환하는 엔드포인트 경로와 필드명

이전 step에서 만들어진 API 계약을 확인한 뒤 작업하라. 필요하면 `backend/app/api/schemas.py`의 응답 모델을 직접 읽어 필드명을 맞춘다.

## 작업

문서 상세의 관련 문서·태그 추천 자리표시를 실제 API에 연결한다. **이 step에서 태그 추천은 표시만 한다** — 클릭해서 태그를 적용하는 것은 step 6이다.

### 1) 타입 (`frontend/src/lib/types.ts`)

`RelatedDocument`, `IdenticalDocument`, `RelatedResponse`, `TagSuggestion`, `TagSuggestionsResponse`를 백엔드 응답 모델과 **snake_case 그대로** 추가한다(기존 타입들과 같은 규약).

### 2) API 클라이언트 (`frontend/src/lib/api.ts`)

```typescript
export function getRelated(id: string, k?: number): Promise<RelatedResponse>
export function getTagSuggestions(id: string, limit?: number): Promise<TagSuggestionsResponse>
```

기존 `request<T>()` 헬퍼를 재사용한다 — `X-User-Id` 헤더 주입과 `ApiError` 변환이 이미 그 안에 있다. 새로 `fetch`를 부르지 마라.

### 3) 데이터 훅 (`frontend/src/lib/useRelated.ts`)

```typescript
export function useRelated(id: string, chunkVersion: number | null): {
  related: RelatedResponse | null;
  suggestions: TagSuggestionsResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}
```

- 두 API를 병렬로 호출한다
- **`chunkVersion`이 바뀌면 다시 불러온다.** 이유: 업로드 직후에는 `not_indexed`였다가 임베딩이 끝나면 결과가 생긴다. `useDocument`가 이미 `pending`/`processing`일 때 2초 폴링을 하므로, 색인이 끝나는 순간 `chunk_version`이 `null → 1`로 바뀐다. 그 변화를 트리거로 삼는다
- **자체 폴링 타이머를 두지 마라.** 이유: `useDocument`의 폴링과 겹쳐 같은 화면에서 두 개의 타이머가 돈다
- `refresh`를 노출한다 — step 6이 태그 적용 후 추천을 갱신하는 데 쓴다
- 언마운트 후 setState를 막는 `mountedRef` 패턴은 `useDocument.ts`를 따른다

### 4) 컴포넌트

**`frontend/src/components/RelatedDocuments.tsx`**

- 각 항목: 문서 제목(상세 링크), 태그, **점수**, **"v{n} 기준"**(`based_on_version`)
- 섹션 제목 위 또는 목록 상단에 **"내용이 유사한 문서가 있습니다"** 문구를 쓴다
- `identical`이 비어 있지 않으면 별도 영역에 **"동일한 텍스트의 문서가 있습니다"** + 제목 링크만 제공한다
- `reason === "not_indexed"`이면 목록 대신 안내를 보인다:
  > 임베딩이 완료되면 표시됩니다.

  **오류로 보이게 하지 마라** — 빨간색·경고 아이콘을 쓰지 않고 보조 텍스트(`text-neutral-500`)로 둔다
- 결과가 0건이면(색인은 됐으나 이웃이 없음) 빈 상태 문구를 따로 보인다

**`frontend/src/components/TagSuggestions.tsx`**

- 태그 칩 목록. 각 칩에 빈도를 함께 보여도 좋다
- 이 step에서는 **읽기 전용**이다(버튼이 아니라 표시). step 6이 클릭 핸들러를 붙인다
- `not_indexed`·빈 결과 처리는 위와 동일

각 컴포넌트에 대응하는 `*.test.tsx`를 **먼저** 작성한다.

### 5) 상세 페이지 연결 (`frontend/src/app/documents/[id]/page.tsx`)

51~61행의 두 자리표시 `<section>`과 그 위의 `{/* M4에서 ... 교체한다 */}` 주석을 제거하고 실제 컴포넌트로 바꾼다. `useRelated(id, document.chunk_version)`로 데이터를 가져와 넘긴다.

## 문구 규칙 (어기면 이 step은 실패다)

- **"중복"이라고 쓰지 마라.** 점수는 비대칭 지표라 중복을 확정할 수 없다 (ADR-018). 허용되는 문구는 "내용이 유사한 문서가 있습니다"(점수 상위)와 "동일한 텍스트의 문서가 있습니다"(`content_hash` 일치)뿐이다
- 유사 항목에 자동 차단·병합·경고 배지를 붙이지 마라. **링크만 제공한다**
- **"항상 최신"·"실시간 동기화"를 쓰지 마라** (ADR-015). 보장 범위는 버전 일관성 + 최신 수렴이다

## 테스트

`frontend/src/components/RelatedDocuments.test.tsx`, `TagSuggestions.test.tsx`, `frontend/src/lib/useRelated.test.ts`를 **먼저** 작성한다. 최소한 아래를 덮어야 한다.

- 관련 문서 목록이 점수·"v{n} 기준"과 함께 렌더링되고 각 항목이 `/documents/{id}`로 링크된다
- `reason === "not_indexed"`면 "임베딩이 완료되면 표시됩니다." 안내가 나오고 목록은 렌더링되지 않는다
- `identical`이 있으면 "동일한 텍스트의 문서가 있습니다"가 나온다
- **"중복"이라는 단어가 렌더 결과에 없다**
- `useRelated`가 두 API를 부르고, `chunkVersion`이 바뀌면 다시 부른다 (같은 값으로 리렌더될 때는 다시 부르지 않는다)
- API 실패 시 화면이 깨지지 않고 에러 문구를 노출한다

## Acceptance Criteria

```bash
cd frontend
npm run lint      # 통과
npm test          # 전부 통과
npm run build     # 타입 에러 없음 (TypeScript strict)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - UI_GUIDE의 문구 규칙을 지켰는가? ("중복" 금지, not_indexed 안내가 오류처럼 보이지 않는가)
   - 기존 `request()`·`useDocument` 패턴을 재사용했는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m4-mcp-gateway/index.json`의 step 5를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (컴포넌트·훅 이름과 props 형태 포함 — step 6이 여기에 클릭 핸들러를 붙인다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 태그 추천을 클릭 가능하게 만들지 마라. 이유: 태그 적용은 step 6의 범위이고, 이 step에는 `updateTags` API 클라이언트가 아직 없다
- `useRelated`에 자체 폴링 타이머를 두지 마라. 이유: `useDocument`의 폴링과 중복된다
- 백엔드 응답 필드명을 프론트에서 camelCase로 바꾸지 마라. 이유: 기존 타입들이 모두 snake_case 그대로다
- 점수를 임계값으로 잘라 "중복 가능성 높음" 같은 배지를 만들지 마라 (ADR-018)
- 서버 컴포넌트에서 사용자별 데이터를 가져오지 마라. 이유: `X-User-Id`는 브라우저의 데모 사용자 저장소에서 온다 — 기존 화면들과 같이 클라이언트 컴포넌트로 둔다
- 기존 테스트를 깨뜨리지 마라
