# Step 2: finder-client

프런트 API 클라이언트와 `useDocuments` 훅이 찾기 조건(제목 검색어·유형·태그·정렬)과 같은 조건의 건수를 다루게 한다. 화면(컴포넌트·페이지)은 step 3이 한다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- step 1이 연 API:
  - `GET /api/documents?status&extraction_status&tag&q&content_type&sort&limit&offset` → `DocumentSummary[]`. `sort`는 `"updated"`(기본, 최근 수정순) | `"title"`(제목순). `content_type`은 소문자 확장자(`hwp` 등).
  - `GET /api/documents/count?status&extraction_status&tag&q&content_type` → `{"total": number}`
  - `GET /api/documents/tags` → `string[]` (볼 수 있는 문서의 태그, 정렬됨)
- 현재 `listDocuments(params, signal)`(`frontend/src/lib/api.ts` 383행 근처)는 `status·tag·limit·offset`만 쿼리로 만든다. `useDocuments`(`frontend/src/lib/useDocuments.ts`)는 `status·limit·offset`을 받아 2초마다 폴링하고, 마운트 동안 `AbortController` 하나로 요청을 묶는다(ADR-048 결정 4).
- 목록 화면은 지금 `/progress` 합계를 전체 수로 쓴다. step 3이 이것을 **조건 건수**로 바꾼다 — 이 step은 그 재료(훅이 건수도 함께 돌려줌)를 만든다.

## 읽어야 할 파일

- `frontend/src/lib/api.ts`, `frontend/src/lib/api.test.ts` — `listDocuments`, `request` 헬퍼, 기존 테스트 방식
- `frontend/src/lib/useDocuments.ts`와 그 테스트(있으면) — 폴링·취소 구조
- `frontend/src/lib/types.ts` — `DocumentSummary`, `ContentType`, `SUPPORTED_CONTENT_TYPES`
- `/docs/UI_GUIDE.md`

## 작업

### 1) 테스트 먼저

- `frontend/src/lib/api.test.ts`:
  1. `listDocuments({ q: "출장", contentType: "hwp", tag: "보안", sort: "title", limit: 50, offset: 0 })`가 `q`·`content_type`·`tag`·`sort`·`limit`·`offset`을 쿼리로 보낸다. 값이 없거나 빈 문자열(공백뿐 포함)인 조건은 넣지 않는다.
  2. `countDocuments({...같은 필터})` → `/api/documents/count?...`를 부르고 `total` 숫자를 돌려준다. `sort·limit·offset`은 보내지 않는다.
  3. `listDocumentTags()` → `/api/documents/tags`.
- `useDocuments` 테스트(새 파일이면 `frontend/src/lib/useDocuments.test.ts`):
  4. 필터를 주면 목록과 건수를 **같은 필터로** 요청하고 `{ documents, total }`을 돌려준다.
  5. 필터가 바뀌면 새 조건으로 다시 요청한다.
  6. 언마운트하면 진행 중 요청이 취소된다(기존 동작 유지).

### 2) 구현

```ts
export type DocumentSort = "updated" | "title";
export interface DocumentFilters { q?: string; contentType?: ContentType; tag?: string; sort?: DocumentSort; status?: EmbeddingStatus; }

export function listDocuments(params?: DocumentFilters & { limit?: number; offset?: number }, signal?: AbortSignal): Promise<DocumentSummary[]>;
export function countDocuments(params?: Omit<DocumentFilters, "sort">, signal?: AbortSignal): Promise<number>;
export function listDocumentTags(signal?: AbortSignal): Promise<string[]>;

export function useDocuments(params?: DocumentFilters & { limit?: number; offset?: number; intervalMs?: number }):
  { documents: DocumentSummary[]; total: number | null; loading: boolean; error: string | null; refresh: () => void };
```

- 훅은 한 번의 폴링 주기에 목록과 건수를 함께 요청한다(같은 `signal`). 건수 요청이 실패하면 `total`은 `null`, 오류 문구는 기존 방식대로.
- 기존 호출부(`useDocuments({limit, offset})`)는 그대로 동작해야 한다.

## Acceptance Criteria

```bash
cd frontend && npm test -- --run src/lib
cd frontend && npm run lint
cd frontend && npx tsc --noEmit
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: `countDocuments`에 `q`를 빼고 보내면 테스트 2·4가 실패해야 한다.
3. `phases/m24-doc-finder/index.json`의 step 2를 갱신한다.

## 금지사항

- 목록 응답을 받아 클라이언트에서 거르거나 정렬하지 마라. 이유: 필터는 SQL에서 건다(CLAUDE.md CRITICAL) — 페이지 경계가 틀어진다.
- 컴포넌트·페이지(`app/page.tsx`, `components/`)를 이 step에서 고치지 마라. 이유: step 3의 범위다(한 step 한 레이어).
- 기존 테스트를 깨뜨리지 마라
