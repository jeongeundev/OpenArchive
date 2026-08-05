# Step 5: text-editing

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ADR.md` — **ADR-017**(인라인 편집은 추출 텍스트의 새 논리 버전을 만든다). 이 step의 근거 문서다
- `/docs/UI_GUIDE.md` — **"추출 텍스트 편집" 절 전체**(레이블·상시 안내·409 문구·편집 중 액션 비활성화), 용어 규칙
- `/docs/ARCHITECTURE.md` — "인라인 편집과 낙관적 동시성" 절, "임베딩 실패 복구" 절, "정합성 보장" 절
- `/docs/PRD.md` — 핵심 기능 3번(텍스트 버전 관리와 자동 재임베딩)
- `frontend/src/lib/api.ts` — step 0의 `editDocument`·`deleteDocument`·`reembedDocument`와 `ApiError.currentVersion`
- `frontend/src/lib/useDocument.ts`, `frontend/src/components/DocumentMeta.tsx`, `frontend/src/components/VersionHistory.tsx`, `frontend/src/app/documents/[id]/page.tsx` — step 4 산출물
- `backend/app/api/documents.py`, `backend/app/services/documents.py` — 편집·삭제·재임베딩의 실제 동작과 권한 검사

## 배경 — 이 화면이 이 프로젝트의 데모다

정합성 파이프라인을 화면에서 직접 보여주는 유일한 수단이다(이슈 #8, ADR-017). 한 문장을 고치고 저장하면:

```
저장 → documents.version +1 → (트리거) 텍스트 버전 이력 기록 + 재임베딩 잡 생성
     → 상태 배지 pending → processing → ready
     → /admin/status 정합성 카운터 0 → 1 → 0
```

**애플리케이션은 `documents`만 UPDATE한다.** 버전 이력과 잡 생성은 DB 트리거가 한다. 프론트는 `PUT` 한 번을 보낼 뿐 임베딩 관련 요청을 하지 않는다.

## 백엔드 계약

| 요청 | 결과 |
|---|---|
| `PUT /api/documents/{id}` JSON `{content, version}` | 200 `DocumentSummary & {content}` |
| 같은 요청, `version`이 서버와 다름 | **409** `{detail, current_version}` |
| `DELETE /api/documents/{id}` | 204 (본문 없음) |
| `POST /api/documents/{id}/reembed` | 200 `DocumentSummary` |

- 세 요청 모두 `X-User-Id` **필수**. 없으면 400
- 타인의 **private** 문서 → 404, 타인의 **public** 문서 → 403(수정 권한 없음)
- 추출 텍스트가 공백뿐이면 DB `CHECK` 제약과 서비스 계층이 막는다(400)

## 작업

### 1. `frontend/src/components/TextEditor.tsx` + `TextEditor.test.tsx`

step 4에서 읽기 전용으로 두었던 추출 텍스트 영역을 대체한다.

```tsx
export function TextEditor({
  document,          // DocumentDetail
  onSaved,           // () => void — 저장 성공 후 상세 재조회
  onEditingChange,   // (editing: boolean) => void — 부모가 다른 액션을 비활성화한다
  disabled,          // boolean — 익명 상태
}: {...}): React.ReactElement;
```

**레이블과 안내**

- 영역 레이블은 **"추출 텍스트"**다. "본문"·"원문"이라고 쓰지 마라(UI_GUIDE 용어 규칙)
- `content_type`이 `pdf` 또는 `docx`면 편집 영역 위에 **상시 표시**한다(접었다 펴는 툴팁이 아니라 항상 보이는 한 줄):

  > 원본 파일이 아니라 업로드 시 추출된 텍스트를 편집합니다. 저장하면 새 텍스트 버전이 만들어집니다.

**보기 ↔ 편집 토글**

- 기본은 보기 모드(`whitespace-pre-wrap`). "편집" 버튼으로 `<textarea>`로 전환
- 편집 모드에서는 **저장·취소만 노출**하고, `onEditingChange(true)`로 부모에 알려 삭제·재임베딩을 비활성화하게 한다(UI_GUIDE 편집 절)
- 취소하면 원래 `content`로 되돌리고 보기 모드로 간다

**저장과 409**

- 저장 시 화면이 읽어온 `document.version`을 함께 보낸다
- 성공 → 보기 모드로 돌아가고 `onSaved()` 호출
- **409를 받으면 덮어쓰지 않는다.** 다음 세 가지를 모두 지켜라:
  1. 편집 모드를 유지하고 **사용자가 입력한 내용을 그대로 남긴다**(복사해 둘 수 있어야 한다)
  2. 안내를 표시한다: "다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요."
  3. `ApiError.currentVersion`이 있으면 함께 알린다(예: "현재 서버 버전: v3")
- **409를 받고 자동으로 재시도하지 마라.** 이유: 재시도는 앞선 변경을 조용히 덮어쓰는 것과 같다. 낙관적 동시성을 넣은 이유가 그것을 막는 데 있다(ADR-017)
- 다른 실패(400·403·404)는 `ApiError.detail`을 그대로 보여준다

**익명 상태(`disabled`)** — 편집 버튼을 비활성화하고 "사용자를 선택하면 편집할 수 있습니다."를 보인다. 요청을 보내 400을 받아 보여주지 마라.

테스트에서 최소한 검증할 것:
- `pdf` 문서면 안내 문구가 편집 여부와 무관하게 보인다. `txt`면 보이지 않는다
- 저장 시 `PUT`이 `{content, version}`으로 불린다
- **409 응답 후에도 textarea에 사용자가 입력한 값이 남아 있고 충돌 안내가 보인다**
- 성공 시 `onSaved`가 불린다
- `disabled`면 편집 버튼이 비활성화된다

### 2. `frontend/src/components/DocumentActions.tsx` + `DocumentActions.test.tsx`

```tsx
export function DocumentActions({
  document, disabled, onChanged,
}: { document: DocumentSummary; disabled: boolean; onChanged: () => void }): React.ReactElement;
```

- **삭제**: 확인을 한 번 받고(`window.confirm` 사용 가능) `deleteDocument` 호출 → 성공하면 `useRouter().push("/")`로 목록으로 이동. 확인 문구에 "청크와 벡터도 함께 삭제됩니다"를 포함한다(CASCADE로 원자 삭제된다 — ARCHITECTURE API 설계)
- **재임베딩**: `embedding_status`가 `error`일 때만 노출한다. 성공하면 `onChanged()`로 상세를 재조회한다. 이유: 정상 문서에 재임베딩 버튼을 상시 노출하면 사용자가 파이프라인을 수동으로 돌리는 것으로 오해한다 — 이 프로젝트의 주장은 그 반대다
- `disabled`(익명 또는 편집 중)면 두 버튼 모두 비활성화
- 403·404는 `ApiError.detail`을 그대로 보여준다. **타인의 public 문서에서는 403이 정상 동작이다** — 오류를 숨기지 말고 그대로 알린다

테스트: 삭제 확인 후 `DELETE`가 불린다 / `error` 상태에서만 재임베딩 버튼이 보인다 / `disabled`면 둘 다 비활성화된다.

### 3. `frontend/src/app/documents/[id]/page.tsx` 갱신

- step 4의 읽기 전용 추출 텍스트 영역을 `<TextEditor />`로 교체
- `<DocumentActions />`를 상단(제목 옆) 또는 하단에 배치
- 편집 중 여부를 `useState`로 들고 있다가 `DocumentActions`의 `disabled`에 반영한다. 이 조립은 page.tsx에 두어도 되지만, **저장·삭제·재임베딩 로직 자체를 여기 인라인하지 마라**
- `onSaved`·`onChanged`는 step 4의 `useDocument().refresh`에 연결한다. 저장 직후 상태가 `pending`으로 바뀌고 조건부 폴링이 다시 돌기 시작해야 한다 — **이 연결이 데모의 핵심 장면이다**

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
   - 409에서 편집 내용이 보존되는가? 자동 재시도를 하지 않는가?
   - 편집 영역 레이블이 "추출 텍스트"이고, pdf/docx 안내가 상시 표시되는가?
   - 편집 중 삭제·재임베딩이 비활성화되는가?
   - 프론트가 `embedding_jobs`를 건드리지 않는가? (`PUT` 한 번이 전부다)
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m3-frontend/index.json`의 step 5를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **409에서 사용자 입력을 지우거나 자동으로 다시 보내지 마라.** 이유: 앞선 변경을 조용히 덮어쓰는 것을 막는 것이 낙관적 동시성의 목적이다(ADR-017)
- **자동 저장·주기적 임시 저장을 만들지 마라.** 이유: 매 저장이 새 텍스트 버전과 재임베딩 잡을 만든다. 자동 저장은 이력을 오염시키고 워커를 불필요하게 돌린다
- **파일 재업로드 UI를 만들지 마라.** 백엔드 `PUT`은 `{content, version}` JSON만 받는다. multipart 경로가 없다. 새 파일을 올리려면 업로드 후 이전 문서를 삭제하는 것이 현재 동선이다
- **임베딩 완료를 기다리는 모달·로딩을 만들지 마라.** 저장 응답은 즉시 오고, 재임베딩 진행은 상태 배지로 보인다
- **`embedding_jobs`를 조회·생성하는 요청을 만들지 마라.** 이유: 잡 생성은 DB 트리거의 책임이다(CLAUDE.md CRITICAL). 재임베딩도 `POST /reembed` 하나로 끝나며, 그 안에서 백엔드가 `content_hash`를 SET 절에 언급해 트리거를 깨운다
- **"항상 최신"·"실시간 동기화"라고 쓰지 마라.** 저장 후 문구는 "임베딩이 끝나면 완료로 바뀝니다" 수준으로 쓴다(ADR-015)
- 기존 테스트를 깨뜨리지 마라
