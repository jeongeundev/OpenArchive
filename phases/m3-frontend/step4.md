# Step 4: document-detail

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/UI_GUIDE.md` — 화면 구성 2번(`/documents/[id]`), **용어 규칙 표**, 관련 문서·태그 추천 절, 색상표
- `/docs/ARCHITECTURE.md` — "DB 스키마" 절의 **`document_chunks.version`의 용도**, "정합성 보장" 절, "API 설계" 절
- `/docs/ADR.md` — **ADR-017**(인라인 편집은 추출 텍스트의 새 논리 버전), ADR-015(보장 범위), ADR-018(관련 문서 — 이 step에서는 자리만 만든다)
- `frontend/src/lib/api.ts`, `types.ts` — step 0 산출물(`getDocument`, `DocumentDetail`)
- `frontend/src/lib/useDocuments.ts` — step 2의 폴링 훅. 같은 패턴을 따르되 상세는 조건부 폴링이다
- `frontend/src/components/StatusBadge.tsx` — step 1 산출물
- `frontend/src/app/page.tsx` — step 2~3에서 만든 목록 화면(조립 방식 참고)
- `frontend/node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/dynamic-routes.md` — **Next 16에서 `params`는 Promise다.** Client Component 페이지에서는 React의 `use()`로 푼다

## 배경 — 세 가지를 구분해서 표기한다

이 화면은 프로젝트 용어 규칙이 가장 자주 깨지는 곳이다(ADR-017).

| 화면에 쓸 말 | 쓰지 말 것 |
|---|---|
| 추출 텍스트 | 본문, 원문 |
| 텍스트 버전 이력 | 문서 버전 이력 |
| 문서 상세 | 원문 링크 |

원본 파일(PDF/DOCX)은 **보관하지 않는다.** 저장된 것은 추출 텍스트뿐이다. `filename`은 업로드 당시의 이름일 뿐 다운로드할 수 있는 파일이 아니다.

## 백엔드 계약

`GET /api/documents/{id}` → `DocumentDetail`:

```
DocumentSummary의 모든 필드 +
  content: string          // 추출 텍스트 전문
  versions: {version, created_at}[]   // 텍스트 버전 이력
  chunk_count: number      // 현재 저장된 청크 수
  chunk_version: number | null        // 그 청크들이 만들어진 기준 버전 (청크가 없으면 null)
```

- 타인의 private 문서는 **404**, 없는 문서도 404
- **청크 내용을 주는 엔드포인트는 없다.** `chunk_count`와 `chunk_version`만 있다

`chunk_version`이 중요한 이유: `documents.version`과 다르면 재임베딩이 아직 안 끝난 것이다. 이 차이가 `/admin/status`의 정합성 카운터(`c.version <> d.version`)와 같은 값을 문서 하나 관점에서 보여준다.

## 작업

### 1. `frontend/src/lib/useDocument.ts` + `useDocument.test.ts`

```ts
export function useDocument(id: string): {
  document: DocumentDetail | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
};
```

- 마운트 시 1회 조회
- **조건부 폴링**: `embedding_status`가 `pending`·`processing`이면 2초마다 재조회하고, `ready`·`error`가 되면 폴링을 멈춘다. 이유: 편집·재임베딩 직후 상태가 바뀌는 것을 보여주는 것이 목적이며, 완료된 문서를 계속 조회할 이유가 없다
- 언마운트 시 타이머를 정리한다
- 폴링 중 실패해도 마지막 성공 데이터를 지우지 않는다(step 2와 같은 이유 — UI_GUIDE 디자인 원칙 3)
- 404는 `error`에 사용자용 문구로 담는다(예: "문서를 찾을 수 없습니다.")

테스트(`vi.useFakeTimers()` + fetch 스텁): `pending`이면 2초 후 재조회한다 / `ready`면 재조회하지 않는다 / 언마운트 후 호출되지 않는다.

### 2. `frontend/src/components/DocumentMeta.tsx` + `DocumentMeta.test.tsx`

```tsx
export function DocumentMeta({ document }: { document: DocumentDetail }): React.ReactElement;
```

표시할 것: 제목, 상태 배지(`<StatusBadge />` 재사용), 파일명, 유형, 공개범위(공개/비공개), 소유자, 태그, 생성·수정 일시, 그리고 **색인 상태 한 줄**.

색인 상태 문구 규칙:

| 조건 | 표시 |
|---|---|
| `chunk_count > 0`이고 `chunk_version === document.version` | "청크 {n}개 · 현재 버전(v{version}) 기준" |
| `chunk_count > 0`이고 `chunk_version !== document.version` | "청크 {n}개 · v{chunk_version} 기준 — 재임베딩 중입니다" |
| `chunk_count === 0` | "아직 색인된 청크가 없습니다." |

- 두 번째 경우가 **이 프로젝트의 핵심 장면**이다. 재임베딩이 끝나기 전에도 이전 버전 청크로 검색이 계속된다는 사실을 문구로 드러낸다. 오류로 보이게 하지 마라 — 빨간색·경고 아이콘을 쓰지 않는다
- 세 번째 경우도 오류가 아니다. `text-neutral-500`으로 둔다
- **청크 목록을 만들지 마라.** 조회 API가 없고, 청크 원문은 바로 아래 추출 텍스트와 중복된다. 이 요약이 청크 목록의 자리를 대신한다

테스트: 세 경우의 문구가 각각 나온다 / 태그가 모두 렌더된다 / 상태 배지가 있다.

### 3. `frontend/src/components/VersionHistory.tsx` + `VersionHistory.test.tsx`

```tsx
export function VersionHistory({
  versions, currentVersion,
}: { versions: TextVersion[]; currentVersion: number }): React.ReactElement;
```

- 섹션 제목은 **"텍스트 버전 이력"**이다. "문서 버전 이력"이라고 쓰지 마라 — 파일 버전으로 읽힌다(UI_GUIDE 용어 규칙)
- 버전 번호 내림차순, 각 항목에 생성 일시. `currentVersion`과 같은 항목에 "현재" 표시
- 되돌리기·비교(diff) 버튼을 만들지 마라 — 백엔드에 해당 API가 없다
- 이력이 비어 있으면(v1만 있고 편집 이력이 없는 경우) "편집 이력이 없습니다." 한 줄

### 4. `frontend/src/app/documents/[id]/page.tsx`

- `"use client"` — 조건부 폴링과 사용자별 조회가 브라우저 상태에 의존한다
- `params`는 **Promise**다. Client Component 페이지에서는 `use(params)`로 푼다(위 Next 16 문서 참조)
- 구성 순서: 상단에 목록으로 돌아가는 링크 → `<DocumentMeta />` → 추출 텍스트 영역 → `<VersionHistory />` → 관련 문서·태그 추천 자리
- **추출 텍스트는 이 step에서 읽기 전용으로 표시한다.** 레이블은 "추출 텍스트", 표시는 `whitespace-pre-wrap`. 편집 기능은 step 5가 이 영역을 대체한다
- 로딩·404 상태를 각각 한 줄 문구로 처리한다
- **page.tsx에는 조립만 둔다**(`tdd-guard.sh`가 `page.tsx`를 테스트 없이 통과시킨다)

### 5. 관련 문서·태그 추천 **자리**

두 섹션의 제목과 안내만 둔다. **API를 호출하지 마라 — `/related`와 `/tag-suggestions`는 M4에서 만든다.**

- 섹션 제목: "관련 문서", "태그 추천"
- 본문: "임베딩이 완료되면 표시됩니다." — `text-neutral-500`, 오류로 보이지 않게(UI_GUIDE 관련 문서 절)
- 로직이 없는 정적 마크업이므로 `page.tsx`에 직접 두어도 된다
- 주석으로 M4에서 교체될 자리임을 남겨라

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

용어·과장 문구 검사(매치가 있으면 실패 — ADR-015, ADR-017):

```bash
cd frontend && ! grep -rn "항상 최신\|실시간 동기화\|무중단\|원문\|문서 버전 이력" src/
```

## 검증 절차

1. 위 AC 커맨드를 전부 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 상세 폴링이 `pending`·`processing`에서만 도는가?
   - 색인 상태 세 문구가 `chunk_count`·`chunk_version`을 정확히 반영하는가?
   - "텍스트 버전 이력" 표기를 쓰는가?
   - 관련 문서·태그 추천 자리가 API를 호출하지 않는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m3-frontend/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (step 5가 추출 텍스트 영역을 대체하므로 그 위치와 훅 사용 방식을 담아라)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **편집·삭제·재임베딩 UI를 만들지 마라.** step 5의 범위다. 이 step의 추출 텍스트 영역은 읽기 전용이다
- **`/related`·`/tag-suggestions`를 호출하지 마라.** M4에서 만드는 엔드포인트라 지금은 404가 난다
- **청크 목록·청크 원문을 표시하지 마라.** 조회 API가 없다. 추측으로 엔드포인트를 만들어 호출하지 마라
- **원본 파일 다운로드 링크를 만들지 마라.** 원본 파일은 보관하지 않는다(ADR-017). `filename`은 표시만 하는 값이다
- **버전 되돌리기·diff를 만들지 마라.** 백엔드에 해당 API가 없다
- **재임베딩 중 상태를 오류처럼 표시하지 마라.** 빨간색·경고 아이콘을 쓰지 않는다. 이유: 이전 버전 청크로 검색이 계속되는 것은 설계된 동작이며, 검색 공백이 없다는 것이 오히려 강점이다(PRD 핵심 기능 3)
- 기존 테스트를 깨뜨리지 마라
