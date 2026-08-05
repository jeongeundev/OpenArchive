# Step 0: api-client

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "API 설계" 절(엔드포인트 표, 빈 파싱 결과 400, 409 낙관적 동시성), "프론트엔드 패턴" 절
- `/docs/UI_GUIDE.md` — 용어 규칙(원본 파일 / 추출 텍스트 / 텍스트 버전)
- `/docs/PRD.md` — MVP 제외 사항(실제 인증 없음, `X-User-Id` 헤더 기반 데모 사용자)
- `backend/app/api/schemas.py` — 응답 모델의 정확한 필드명
- `backend/app/api/documents.py`, `backend/app/api/search.py`, `backend/app/api/system.py` — 실제 요청/응답 형태
- `backend/app/api/deps.py` — `require_user_id`가 헤더 없음을 어떻게 처리하는지
- `backend/app/main.py` — 도메인 예외를 상태 코드로 옮기는 exception handler
- `frontend/next.config.ts` — `/api/*` rewrites 프록시
- `frontend/src/test/setup.ts`, `frontend/vitest.config.ts` — 테스트 환경

이 step은 M3(프론트엔드 4화면)의 첫 단계이며, **화면은 만들지 않는다.** 이후 모든 화면이 쓸 타입·API 클라이언트·데모 사용자 저장소만 만든다.

## 백엔드 계약 (실측 확인된 값 — 추측하지 말고 이대로 맞춰라)

| 메서드 | 경로 | 요청 | 성공 | 사용자 헤더 |
|---|---|---|---|---|
| POST | `/api/documents` | multipart: `file`, `title?`, `tags?`(반복 필드), `visibility?`(`public`\|`private`) | 201 `DocumentSummary` | **필수** |
| GET | `/api/documents` | query: `status?`, `tag?` | 200 `DocumentSummary[]` | 선택 |
| GET | `/api/documents/{id}` | — | 200 `DocumentDetail` | 선택 |
| PUT | `/api/documents/{id}` | JSON `{content, version}` | 200 `DocumentSummary & {content}` | **필수** |
| DELETE | `/api/documents/{id}` | — | 204 (본문 없음) | **필수** |
| POST | `/api/documents/{id}/reembed` | — | 200 `DocumentSummary` | **필수** |
| POST | `/api/search` | JSON `{query, tags?, content_type?, k?}` | 200 `{items, sql}` | 선택 |
| GET | `/api/system/status` | — | 200 `SystemStatus` | 선택 |

에러 응답은 전부 `{"detail": "..."}` 형태이며, **409만 `current_version`을 함께 담는다**:

```json
{ "detail": "다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요.", "current_version": 3 }
```

`X-User-Id`가 없을 때 쓰기 요청은 **400**을 반환한다(401이 아니다). `require_user_id`는 `x_user_id is None`만 검사하므로 **빈 문자열은 통과해 `owner_id = ""`인 문서를 만든다** — 익명 상태에서는 헤더 자체를 보내지 마라.

DB 스키마상 허용값은 다음뿐이다:
- `embedding_status`: `pending` | `processing` | `ready` | `error`
- `visibility`: `public` | `private`
- `content_type`: `pdf` | `docx` | `txt` | `md`

`k`의 상한은 백엔드 `app/services/search.py`의 `MAX_K = 20`이며, 넘기면 422가 난다.

## 작업

### 1. `frontend/src/lib/types.ts`

백엔드 `schemas.py`에 1:1 대응하는 타입만 둔다. **필드명은 백엔드의 snake_case를 그대로 쓴다** — 변환 계층을 만들지 마라. 이유: 매핑 코드가 늘면 백엔드 스키마 변경 시 어긋나는 지점이 두 곳이 된다.

```ts
export type EmbeddingStatus = "pending" | "processing" | "ready" | "error";
export type ContentType = "pdf" | "docx" | "txt" | "md";
export type Visibility = "public" | "private";

export interface DocumentSummary { /* id, title, filename, content_type, version,
  owner_id, visibility, tags, embedding_status, created_at, updated_at */ }
export interface TextVersion { /* version, created_at */ }
export interface DocumentDetail extends DocumentSummary { /* content, versions,
  chunk_count, chunk_version */ }
export interface SearchResult { /* document_id, title, tags, content_type,
  chunk_index, content, score */ }
export interface SearchResponse { items: SearchResult[]; sql: string }
export interface JobCounts { pending: number; processing: number; error: number }
export interface SystemStatus { /* node_address, node_port, jobs,
  inconsistent_documents, embedding_provider, reconnect_events */ }
```

`nullable`인 필드를 정확히 옮겨라: `filename`·`chunk_version`·`node_address`는 `null`이 올 수 있고, `reconnect_events`는 항상 `null`이다(M5에서 채운다).

`SUPPORTED_CONTENT_TYPES`(4종 배열)와 `MAX_K = 20`도 여기 둔다. `MAX_K`에는 근거를 주석으로 남겨라 — 백엔드 `app/services/search.py`의 `MAX_K`와 같은 값이어야 하며 넘기면 422다.

### 2. `frontend/src/lib/user.ts` + `user.test.ts`

데모 사용자를 브라우저에 보관한다. 실제 인증이 아니다(PRD MVP 제외 사항).

```ts
export const DEMO_USERS: readonly string[];        // 프리셋 2명
export function getCurrentUser(): string | null;   // null = 익명
export function setCurrentUser(user: string | null): void;
```

- 저장소는 `localStorage`, 키는 모듈 상수로 한 번만 정의한다
- **익명은 유효한 상태다.** `null`이면 저장 항목을 지운다
- `typeof window === "undefined"`면 `getCurrentUser()`는 `null`을 반환한다 — 서버 렌더링 중 호출돼도 던지지 않아야 한다
- 저장된 값이 `DEMO_USERS`에 없으면 `null`로 취급한다(손상된 localStorage 방어)

### 3. `frontend/src/lib/api.ts` + `api.test.ts`

```ts
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly currentVersion?: number;   // 409에서만 채워진다
}

export function listDocuments(params?: { status?: EmbeddingStatus; tag?: string }): Promise<DocumentSummary[]>;
export function getDocument(id: string): Promise<DocumentDetail>;
export function uploadDocument(input: {
  file: File; title?: string; tags: string[]; visibility: Visibility;
}): Promise<DocumentSummary>;
export function editDocument(id: string, input: { content: string; version: number }): Promise<DocumentSummary & { content: string }>;
export function deleteDocument(id: string): Promise<void>;
export function reembedDocument(id: string): Promise<DocumentSummary>;
export function search(input: { query: string; tags?: string[]; contentType?: ContentType | null; k?: number }): Promise<SearchResponse>;
export function getSystemStatus(): Promise<SystemStatus>;
```

핵심 규칙:

- **호출 경로는 상대 경로 `/api/...`다.** 절대 URL이나 백엔드 주소 환경변수를 프론트 코드에 넣지 마라. 이유: `next.config.ts`의 rewrites가 이미 `/api/*`를 FastAPI로 프록시하며, 주소를 두 곳에서 관리하면 배포 시 어긋난다
- **모든 요청은 `getCurrentUser()`를 읽어 `X-User-Id`를 붙인다. `null`이면 헤더를 생략한다** — 빈 문자열을 보내면 백엔드가 통과시켜 `owner_id = ""`인 문서가 생긴다
- 응답이 `ok`가 아니면 **항상 `ApiError`를 던진다.** 본문에서 `detail`을 꺼내 쓰고, 본문이 JSON이 아니면 상태 코드 기반의 짧은 한국어 메시지를 쓴다
- 409면 본문의 `current_version`을 `ApiError.currentVersion`에 담는다. **이 값이 없으면 편집 화면이 충돌을 안내할 수 없다**
- `uploadDocument`는 `FormData`로 보낸다. `tags`는 **같은 키로 여러 번 append**한다(백엔드가 `list[str]` Form으로 받는다). 빈 배열이면 append하지 않는다. `Content-Type`을 직접 지정하지 마라 — 브라우저가 boundary를 붙인다
- `deleteDocument`는 204라 본문이 없다. `response.json()`을 호출하지 마라
- `search`의 `contentType`은 요청 본문에서 **`content_type`** 키로 보낸다. 값이 없으면 키를 생략하거나 `null`을 보낸다

### 테스트에서 지킬 것

`vi.stubGlobal("fetch", vi.fn())`으로 스텁한다. **MSW 같은 라이브러리를 추가하지 마라** — 의존성 없이 검증 가능하다.

최소한 아래를 검증하라(테스트가 통과하도록 구현을 약화하지 말 것):

- 사용자가 선택돼 있으면 요청 헤더에 `X-User-Id`가 그 값으로 실린다
- **익명이면 `X-User-Id` 헤더가 아예 없다** (빈 문자열이 아니다)
- 409 응답 → `ApiError.status === 409`이고 `currentVersion`이 본문 값과 같다
- 400 응답 → `ApiError.detail`이 본문의 `detail` 문자열과 같다
- `uploadDocument`가 `FormData`를 보내고 태그 2개가 모두 실린다
- `listDocuments({ status: "error" })`가 쿼리스트링에 `status=error`를 넣는다
- `deleteDocument`가 204에서 정상 반환한다

## Acceptance Criteria

```bash
cd frontend
npm run lint
npm run test
npm run build
```

추가로, UI 슬롭 안티패턴이 들어가지 않았는지 확인한다(이 step은 UI가 없으므로 통과해야 정상이다):

```bash
cd frontend && ! grep -rEn "backdrop-blur|backdrop-filter|bg-gradient|blur-3xl|purple-|indigo-|violet-|animate-(pulse|bounce|spin|ping)" src/
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 타입 필드명이 `backend/app/api/schemas.py`와 정확히 일치하는가?
   - `X-User-Id`를 익명일 때 생략하는가?
   - 백엔드 주소를 프론트 코드에 하드코딩하지 않았는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m3-frontend/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (다음 step이 쓸 함수 시그니처와 파일 경로를 담아라)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **화면·컴포넌트를 만들지 마라.** 이 step의 산출물은 `src/lib/` 아래 3개 파일과 테스트뿐이다. 이유: 화면은 step 1~7에서 각각 다루며, 미리 만들면 그 step의 설계와 충돌한다
- **MSW·axios·swr·react-query 등 의존성을 추가하지 마라.** 이유: `fetch`와 `vi.stubGlobal`로 충분하며, ARCHITECTURE.md "상태 관리" 절이 전역 상태 라이브러리를 배제한다
- **snake_case → camelCase 변환 계층을 만들지 마라.** 이유: 백엔드 스키마와 어긋나는 지점이 두 곳으로 늘어난다. 함수 인자만 camelCase를 쓰고(`contentType`), 서버로 나가는 본문·응답 타입은 백엔드 표기를 그대로 쓴다
- **에러를 삼키고 `null`을 반환하지 마라.** 화면이 사용자에게 무엇이 잘못됐는지 알릴 수 없게 된다. 항상 `ApiError`를 던진다
- 기존 테스트를 깨뜨리지 마라
