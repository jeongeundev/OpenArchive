# Step 6: tag-editing-ui

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 UI 규칙과 기존 패턴을 파악하라:

- `/docs/UI_GUIDE.md` — **"관련 문서 · 태그 추천"** 절 ("추천은 제안일 뿐 자동 적용하지 않는다")
- `frontend/src/components/TagSuggestions.tsx` — **이전 step의 산출물.** 여기에 클릭 동작을 붙인다
- `frontend/src/lib/useRelated.ts` — 이전 step의 훅. `refresh`를 노출한다
- `frontend/src/components/DocumentActions.tsx` — 쓰기 액션의 `disabled`·에러 표시 관례
- `frontend/src/components/TextEditor.tsx` — 편집 UI의 상태 관리·저장 실패 처리 관례
- `frontend/src/components/DocumentMeta.tsx` — **현재 태그 칩이 여기에 렌더링된다**(읽기 전용)
- `frontend/src/lib/api.ts`, `frontend/src/lib/user.ts` — `request()` 헬퍼와 데모 사용자 저장소
- `frontend/src/app/documents/[id]/page.tsx` — 조립 지점
- `phases/m4-mcp-gateway/index.json`의 step 3 summary — 태그 교체 엔드포인트의 경로와 요청 본문 형태

이전 step들에서 만들어진 코드를 꼼꼼히 읽고, 같은 상태 관리 패턴을 따르라.

## 작업

문서 태그를 편집할 수 있게 하고, 태그 추천을 클릭하면 문서에 적용되도록 연결한다.

### 1) API 클라이언트 (`frontend/src/lib/api.ts`)

```typescript
export function updateTags(id: string, tags: string[]): Promise<DocumentSummary>
```

`PUT /api/documents/{id}/tags`, 본문 `{ tags }`. 기존 `request<T>()` 헬퍼를 재사용한다.

### 2) 태그 편집 컴포넌트 (`frontend/src/components/TagEditor.tsx`)

```typescript
export function TagEditor(props: {
  document: DocumentDetail;
  disabled: boolean;
  onSaved: () => void;      // 문서 refresh
}): React.ReactElement
```

- 현재 태그를 칩으로 보이고, 각 칩에 삭제(×) 버튼을 둔다
- 입력창에서 Enter 또는 추가 버튼으로 태그를 더한다
- 서버에는 **전체 목록을 교체**해 보낸다(`updateTags(id, nextTags)`) — 백엔드가 PUT 전체 교체 시맨틱이다
- 저장 성공 시 `onSaved()`로 문서를 다시 불러온다
- 저장 실패(403·404·400)는 화면에 문구로 남긴다. **사용자가 입력한 태그를 지우지 마라** — `TextEditor`의 409 처리와 같은 원칙이다
- 익명 사용자(`getCurrentUser() === null`)나 편집 중일 때의 `disabled` 처리는 `DocumentActions`의 호출 방식을 따른다

**태그 변경은 새 텍스트 버전을 만들지 않고 재임베딩도 일으키지 않는다.** UI에서 저장 후 상태 배지가 `pending`으로 바뀔 것을 기대하지 마라 — 바뀌면 백엔드 버그다. 태그 저장 후에 폴링을 유발하는 코드를 넣지 마라.

### 3) 추천 태그 클릭 연결 (`frontend/src/components/TagSuggestions.tsx`)

- `onApply?: (tag: string) => void` prop을 추가한다. 있으면 칩을 `<button>`으로, 없으면 기존처럼 표시만 한다
- 클릭 시 **현재 태그 + 클릭한 태그**로 `updateTags`를 호출한다(호출 자체는 상세 페이지 또는 `TagEditor`에서 처리해도 좋다 — 한 곳에서만 하라)
- 적용 후 **문서와 추천을 모두 갱신한다.** 추천은 이미 달린 태그를 제외하므로, 갱신하지 않으면 방금 적용한 태그가 추천에 남아 있는다
- 추천을 자동 적용하지 마라. 반드시 클릭이 있어야 한다 (UI_GUIDE)

### 4) 상세 페이지 조립 (`frontend/src/app/documents/[id]/page.tsx`)

`TagEditor`를 배치하고, `useRelated`의 `refresh`를 태그 적용 후 호출되도록 연결한다. `DocumentMeta`의 읽기 전용 태그 칩과 중복 표시가 되지 않도록 정리한다 — **둘 중 한 곳에서만 태그를 보여준다**(편집 가능한 쪽을 남기는 것이 자연스럽다).

## 테스트

`frontend/src/components/TagEditor.test.tsx`와 `TagSuggestions.test.tsx`(기존 파일에 추가)를 **먼저** 작성한다. 최소한 아래를 덮어야 한다.

- 태그를 추가하고 저장하면 `updateTags`가 **전체 목록**으로 호출된다
- 태그 칩의 × 로 태그를 지우고 저장하면 그 태그가 빠진 목록으로 호출된다
- 저장 실패(403) 시 에러 문구가 보이고 **입력 내용이 남는다**
- `disabled`면 입력·버튼이 모두 비활성이다
- 추천 태그를 클릭하면 현재 태그에 그 태그가 더해진 목록으로 `updateTags`가 호출되고, 성공 후 문서·추천 갱신 콜백이 호출된다
- `onApply`가 없으면 추천 칩이 버튼이 아니다(읽기 전용 모드가 유지된다)

API 호출은 `vi.mock`으로 `@/lib/api`를 대체한다(기존 컴포넌트 테스트 관례를 따른다).

## Acceptance Criteria

```bash
cd frontend
npm run lint      # 통과
npm test          # 전부 통과
npm run build     # 타입 에러 없음
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 추천이 자동 적용되지 않고 클릭이 필요한가? (UI_GUIDE)
   - 태그 저장 후 문서와 추천이 모두 갱신되는가?
   - 태그가 두 곳에 중복 렌더링되지 않는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m4-mcp-gateway/index.json`의 step 6을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 태그 변경을 `PUT /api/documents/{id}`(추출 텍스트 편집)로 보내지 마라. 이유: 그 엔드포인트는 `{content, version}` 전용이고, 태그를 실어 보내면 본문이 덮어써진다
- 추천 태그를 자동으로 적용하지 마라. 이유: "추천은 제안일 뿐 자동 적용하지 않는다" (UI_GUIDE)
- 태그 저장 후 임베딩 상태 폴링을 시작하지 마라. 이유: 태그 변경은 재임베딩을 유발하지 않는다. 폴링하면 아무 일도 일어나지 않는 상태를 계속 조회한다
- 저장 실패 시 입력한 태그를 초기화하지 마라. 이유: 사용자가 입력을 잃는다 (`TextEditor`의 409 처리와 같은 원칙)
- 태그 개수·문자 제한 같은 검증을 프론트에만 추가하지 마라. 이유: 백엔드가 하지 않는 검증을 프론트만 하면 API 계약이 어긋난다
- step 5에서 만든 관련 문서 표시 동작을 바꾸지 마라
- 기존 테스트를 깨뜨리지 마라
