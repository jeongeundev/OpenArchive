# Step 3: finder-ui

문서 목록 화면 상단에 **제목 검색 · 문서 유형 · 태그 · 정렬**을 두고, 페이지 나눔을 조건 건수로 한다. 조건에 맞는 문서가 없으면 "조건에 맞는 문서가 없습니다."를 보인다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- 기능명세서 시험항목(제출 뒤 수정 불가 — **문구가 구현 계약이다**, 따옴표 안 문구는 글자 그대로):
  - 목록 상단 「제목 검색」에 "출장"을 입력하면 제목에 "출장"이 들어간 문서만 표시됨
  - 조건에 맞는 문서가 없으면 **"조건에 맞는 문서가 없습니다."**가 표시됨
  - 문서 유형 「HWP」를 고르면 HWP 문서만 표시됨
  - 태그 「보안」을 고르면 그 태그가 붙은 문서만 표시됨
  - 제목 검색어와 유형·태그 필터를 함께 걸면 모든 조건을 만족하는 문서만 표시됨
  - 정렬을 「제목순」으로 바꾸면 제목 가나다순으로 표시됨
  - 정렬이 「최근 수정순」(기본)이면 수정일 최신순으로 표시됨
- step 2가 만든 것(`frontend/src/lib/`): `useDocuments({ q, contentType, tag, sort, limit, offset })` → `{ documents, total, loading, error, refresh }`, `listDocumentTags(signal)`, 타입 `DocumentSort = "updated" | "title"`, `DocumentFilters`.
- 현재 `frontend/src/app/page.tsx`: `PAGE_SIZE = 50`, `useDocumentProgress`의 합계를 `total`로 써서 `DocumentPager`에 넘기고, 합계가 0이면 `EmptyDocuments`(첫 사용 안내)를 보인다. `DocumentTable`은 문서가 0건이면 "아직 문서가 없습니다."를 그린다.
- 유형 칸은 지금 `content_type`을 CSS `uppercase`로 보인다(`hwp` → HWP). 유형 선택지 라벨도 같은 방식(대문자)으로 보인다. 선택지 값은 `SUPPORTED_CONTENT_TYPES`(`types.ts`).
- 기본 정렬 라벨은 「최근 수정순」, 다른 하나는 「제목순」.

## 읽어야 할 파일

- `/docs/UI_GUIDE.md` — 문구·색·간격 규칙
- `frontend/src/app/page.tsx`, `frontend/src/__tests__/page.test.tsx`
- `frontend/src/components/DocumentTable.tsx`·`.test.tsx`, `DocumentPager.tsx`, `EmptyDocuments.tsx`, `SearchForm.tsx`(입력·select 스타일 선례)
- `frontend/src/lib/useDocuments.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` — step 2 결과

## 작업

### 1) 테스트 먼저

- 새 `frontend/src/components/DocumentFilters.test.tsx`:
  1. 「제목 검색」 입력(접근 가능한 이름 "제목 검색"), 문서 유형 선택(「전체」 + 유형들, 라벨 대문자), 태그 선택(「전체」 + 받은 태그), 정렬 선택(「최근 수정순」 기본, 「제목순」)이 보인다.
  2. 각 컨트롤을 바꾸면 `onChange`가 바뀐 필터 객체로 불린다. 제목 입력은 타이핑마다가 아니라 잠깐 멈춘 뒤(디바운스) 한 번 불린다.
- `frontend/src/__tests__/page.test.tsx`에 추가:
  3. 필터를 바꾸면 그 조건으로 목록을 다시 요청하고 페이지가 첫 페이지로 돌아간다.
  4. 문서가 하나라도 있는데 조건 건수가 0이면 **"조건에 맞는 문서가 없습니다."**가 보이고 첫 사용 안내(`EmptyDocuments`)는 보이지 않는다.
  5. 전체 문서가 0건이면(기존 동작) 첫 사용 안내가 보인다.
  6. 페이지 나눔은 조건 건수(`total`)를 쓴다 — 조건 건수 120, 전체 300이면 3페이지.

### 2) 구현

- 새 `frontend/src/components/DocumentFilters.tsx`: `({ value: DocumentFilters, tags: string[], onChange }) => ReactElement`. 태그 목록은 페이지가 `listDocumentTags`로 받아 넘긴다.
- `page.tsx`: 필터 상태를 두고 `useDocuments`에 넘긴다. 필터가 바뀌면 `page`를 0으로. `DocumentPager`의 `total`은 훅의 `total`. 첫 사용 안내 판정은 지금처럼 `/progress` 합계(전체 문서 수)로 한다 — 조건 결과 0과 문서 0을 구분하기 위해서다.
- 조건 결과가 0일 때 문구는 "조건에 맞는 문서가 없습니다."다. `DocumentTable`의 빈 문구를 prop으로 바꿀 수 있게 하거나 페이지에서 따로 그린다 — 재량. 필터가 없을 때의 기존 "아직 문서가 없습니다."는 유지한다.
- 마지막에 `npm run build:static`으로 `backend/openarchive/static`을 다시 만든다(CI가 소스와 static의 차이를 검사한다).

## Acceptance Criteria

```bash
cd frontend && npm test
cd frontend && npm run lint
cd frontend && npx tsc --noEmit
cd frontend && npm run build:static
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: 페이지가 `total` 대신 `/progress` 합계를 `DocumentPager`에 넘기면 테스트 6이 실패해야 한다. 빈 결과 문구를 바꾸면 테스트 4가 실패해야 한다.
3. UI_GUIDE의 문구·스타일 규칙을 따랐는지 확인한다.
4. `phases/m24-doc-finder/index.json`의 step 3을 갱신한다.

## 금지사항

- 받은 목록을 클라이언트에서 거르거나 정렬하지 마라. 이유: 필터는 SQL에서 건다(CLAUDE.md CRITICAL).
- "조건에 맞는 문서가 없습니다."·「제목 검색」·「최근 수정순」·「제목순」 문구를 바꾸지 마라. 이유: 명세서 시험항목 문구다 — 제출 뒤 고칠 수 없다.
- 폴더 UI를 만들지 마라. 이유: 다음 phase(m25-folders 이후)의 범위다.
- `backend/openarchive/static`을 손으로 고치지 마라. 이유: `build:static` 산출물이다.
- 기존 테스트를 깨뜨리지 마라
