# Step 6: search-page

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/UI_GUIDE.md` — 화면 구성 3번(`/search`), 색상표(포인트 색 `#0ea5e9`), 타이포그래피(코드/SQL 표시), 용어 규칙, 애니메이션(fade-in은 검색 결과 갱신 시에만 허용)
- `/docs/ARCHITECTURE.md` — **"검색 데이터 흐름" 절 전체**(단일 하이브리드 SQL, 네 가지 설계 결정)
- `/docs/ADR.md` — ADR-010(plain BEGIN), ADR-011(`ef_search`·`DISTINCT ON`), ADR-018(점수는 비대칭 지표)
- `/docs/PRD.md` — 핵심 기능 2번(하이브리드 시맨틱 검색)
- `frontend/src/lib/api.ts`, `types.ts` — step 0의 `search`, `SearchResponse`, `MAX_K`, `SUPPORTED_CONTENT_TYPES`
- `frontend/src/components/StatusBadge.tsx` — step 1(필요 시 재사용)
- `frontend/src/app/page.tsx` — step 2~3의 조립 방식 참고
- `backend/app/api/search.py`, `backend/app/services/search.py` — 요청 필드명과 `SEARCH_SQL`이 응답에 실리는 방식

## 배경 — 이 화면이 보여주는 것

정형 필터(태그·유형·권한)와 벡터 유사도를 **단일 SQL**로 결합한 검색이다. 애플리케이션이 넓게 가져와 후처리하지 않는다(CLAUDE.md CRITICAL). "실행된 SQL 보기" 토글은 그 사실을 심사위원이 직접 확인하는 장치다 — 응답의 `sql` 필드가 실제로 실행된 쿼리 텍스트다.

**권한 필터도 같은 SQL 안에 있다.** 익명으로 검색하면 `owner_id = NULL`이 SQL에서 false로 평가돼 public 문서만 남는다. 프론트에서 결과를 걸러내지 마라.

## 백엔드 계약

`POST /api/search`

```
요청  { query: string, tags?: string[] | null, content_type?: string | null, k?: number }
응답  { items: SearchResult[], sql: string }

SearchResult = { document_id, title, tags, content_type, chunk_index, content, score }
```

- `k`의 상한은 **20**(`MAX_K`). 넘기면 422다. step 0의 `MAX_K` 상수를 쓰고 숫자를 다시 적지 마라
- `X-User-Id`는 선택. 없으면 public 문서만 검색된다
- `score`는 `1 - 코사인 거리`이며 1에 가까울수록 유사하다
- 결과는 **문서당 1건**이다(`DISTINCT ON`). 같은 문서의 여러 청크가 나오지 않는다

## 작업

### 1. `frontend/src/lib/useSearch.ts` + `useSearch.test.ts`

```ts
export interface SearchInput {
  query: string; tags: string[]; contentType: ContentType | null; k: number;
}

export function useSearch(): {
  response: SearchResponse | null;
  loading: boolean;
  error: string | null;
  run: (input: SearchInput) => void;
};
```

- **폴링하지 않는다.** 사용자가 제출할 때만 요청한다
- 요청 중에는 `loading: true`. 실패하면 `error`에 `ApiError.detail`을 담고 이전 결과는 유지한다
- 빈 질의로 요청을 보내지 마라(백엔드에 불필요한 부하이며 결과도 무의미하다)

테스트: `run` 호출 시 `POST /api/search`가 입력값으로 불린다 / 실패 시 `error`가 채워지고 이전 `response`가 남는다.

### 2. `frontend/src/components/SearchForm.tsx` + `SearchForm.test.tsx`

```tsx
export function SearchForm({
  onSearch, pending,
}: { onSearch: (input: SearchInput) => void; pending: boolean }): React.ReactElement;
```

- 질의 입력(필수), 태그(쉼표 구분 → 배열. 공백 제거, 빈 항목 제외), 유형 셀렉트(전체 + `SUPPORTED_CONTENT_TYPES`의 4종), 결과 수 `k`(기본 10, 1~`MAX_K`)
- 유형 목록과 `k` 상한을 하드코딩하지 마라 — step 0의 상수에서 만든다
- `pending`이면 제출 버튼을 비활성화한다
- 활성 필터는 포인트 색(`#0ea5e9`)으로 구분해도 좋다. **그 외의 색을 새로 도입하지 마라**(UI_GUIDE 색상표에 포인트 색은 하나뿐이다)

테스트: 태그 문자열이 배열로 변환돼 `onSearch`에 전달된다 / 유형 "전체"는 `null`로 전달된다 / `k` 입력이 `MAX_K`를 넘지 않는다.

### 3. `frontend/src/components/SearchResults.tsx` + `SearchResults.test.tsx`

```tsx
export function SearchResults({
  response, loading, error,
}: { response: SearchResponse | null; loading: boolean; error: string | null }): React.ReactElement;
```

각 결과 항목:
- 제목 → **`/documents/{document_id}` 링크**. 원본 파일 링크를 만들지 마라(UI_GUIDE 용어 규칙)
- 유형·태그
- **매칭된 청크 발췌**(`content`). 길면 잘라서 보여준다
- **점수**: 소수점 3자리, 포인트 색으로 강조. 레이블은 "유사도" 정도로 쓰되, **"일치율"·"정확도"라고 쓰지 마라** — 코사인 유사도이지 정답률이 아니다
- 검색 결과에는 **"중복"이라고 쓰지 마라**(ADR-018). 점수는 비대칭 지표라 중복을 확정하지 못한다

빈 결과: "검색 결과가 없습니다." — 오류가 아니다(`text-neutral-500`).

**"실행된 SQL 보기" 토글**:
- 기본은 접힌 상태. 펼치면 `response.sql`을 `font-mono text-xs`, `bg-neutral-900` 블록으로 보여준다(UI_GUIDE 타이포그래피)
- SQL을 프론트에서 가공·재구성하지 마라. 서버가 준 문자열을 그대로 보여준다 — **가공하면 "이것이 실제 실행된 쿼리"라는 주장이 깨진다**
- 긴 줄이 잘리지 않도록 가로 스크롤을 허용한다

애니메이션은 결과 갱신 시 fade-in(0.4s)까지만 허용된다(UI_GUIDE).

테스트: 결과 항목이 상세 링크로 렌더된다 / 점수가 표시된다 / 토글을 열면 `sql` 문자열이 나온다 / 빈 배열이면 안내 문구가 나온다.

### 4. `frontend/src/app/search/page.tsx`

- `"use client"`
- `useSearch()` + `<SearchForm />` + `<SearchResults />` 조립
- 페이지 제목과 한 줄 설명. 설명에 "태그·유형 필터와 벡터 유사도를 한 번의 SQL로 검색합니다" 정도를 써도 좋다 — **사실과 다르게 쓰지 마라**
- **page.tsx에는 조립만 둔다**(`tdd-guard.sh`가 `page.tsx`를 테스트 없이 통과시킨다)

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

용어·과장 문구 검사(매치가 있으면 실패 — ADR-015, ADR-017, ADR-018):

```bash
cd frontend && ! grep -rn "항상 최신\|실시간 동기화\|무중단\|원문\|문서 버전 이력\|중복입니다\|일치율" src/
```

## 검증 절차

1. 위 AC 커맨드를 전부 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 프론트에서 결과를 걸러내거나 재정렬하지 않는가? (권한·태그 필터는 SQL이 한다)
   - `sql` 문자열을 가공 없이 그대로 보여주는가?
   - `k` 상한이 step 0의 `MAX_K`에서 오는가?
   - 점수를 "중복"·"일치율"로 표현하지 않는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m3-frontend/index.json`의 step 6을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **결과를 프론트에서 필터링·재정렬·중복 제거하지 마라.** 이유: 정형 필터 + 벡터를 단일 SQL로 결합하는 것이 이 과제의 가산점 항목이다(CLAUDE.md CRITICAL). 후처리를 넣으면 그 주장이 무너진다
- **점수에 임계값을 걸어 결과를 숨기거나 "중복" 배지를 띄우지 마라.** 이유: 점수는 비대칭 지표라 양방향으로 실패한다(ADR-018). 판단은 사람이 한다
- **검색어 자동완성·최근 검색어·무한 스크롤을 만들지 마라.** 요청받지 않은 기능이다
- **SQL을 프론트에서 하이라이팅 라이브러리로 꾸미지 마라.** 의존성이 늘고, 가공하면 원문 그대로라는 신뢰가 떨어진다. `font-mono` 블록으로 충분하다
- **검색 결과를 폴링하지 마라.** 검색은 사용자가 제출할 때만 실행한다
- 기존 테스트를 깨뜨리지 마라
