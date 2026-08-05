# Step 3: upload-dropzone

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/UI_GUIDE.md` — 화면 구성 1번(`/` 목록 + 업로드 드롭존), 용어 규칙(원본 파일 / 추출 텍스트), 컴포넌트 클래스, AI 슬롭 안티패턴
- `/docs/ARCHITECTURE.md` — "API 설계" 절의 **빈 파싱 결과 처리(400)**, "자동 임베딩 파이프라인" 절
- `/docs/ADR.md` — **ADR-017**(원본 파일을 보관하지 않는다), ADR-015(보장 범위)
- `/docs/PRD.md` — 핵심 기능 1번(업로드 → 자동 임베딩), MVP 제외 사항
- `frontend/src/lib/api.ts`, `frontend/src/lib/user.ts`, `frontend/src/lib/types.ts` — step 0 산출물(`uploadDocument`, `getCurrentUser`, `SUPPORTED_CONTENT_TYPES`)
- `frontend/src/lib/useDocuments.ts`, `frontend/src/components/DocumentTable.tsx`, `frontend/src/app/page.tsx` — step 2 산출물
- `backend/app/api/documents.py`, `backend/app/services/parsing.py` — 업로드가 실패하는 두 경우와 그 메시지

## 배경 — 이 화면이 증명하는 것

업로드 코드에는 **임베딩 호출이 없다.** 파일에서 텍스트를 추출해 저장하면 DB 트리거가 잡을 만들고 워커가 처리한다. 사용자가 보는 증거는 "업로드 직후 상태 배지가 `대기 중`으로 나타났다가 잠시 후 `완료`로 바뀐다"는 것 하나다. 드롭존은 그 장면을 만드는 입구다.

**원본 파일은 저장하지 않는다.** 업로드된 PDF/DOCX에서 텍스트만 추출하고 파일 자체는 버린다(ADR-017). 화면 문구가 이를 흐리면 "PDF를 보관한다"는 오해가 생긴다.

## 백엔드 계약

`POST /api/documents` — multipart. 필드: `file`(필수), `title`(선택), `tags`(반복 필드), `visibility`(`public`|`private`, 기본 `public`). `X-User-Id` **필수**(없으면 400).

실패는 세 가지이며 **모두 400이고 `detail`이 이미 사용자용 한국어 문장이다**:

| 경우 | 백엔드 메시지 |
|---|---|
| 확장자가 pdf/docx/txt/md가 아님 | "지원하지 않는 파일 형식입니다: xlsx 지원 형식: pdf, docx, txt, md" |
| 텍스트 추출 결과가 비어 있음(스캔 이미지 PDF 등) | "문서에서 텍스트를 추출하지 못했습니다. …" |
| `X-User-Id` 헤더 없음 | "X-User-Id 헤더가 필요합니다." |

**메시지를 프론트에서 다시 만들지 마라.** `ApiError.detail`을 그대로 보여준다. 이유: 지원 형식 목록 같은 사실이 두 곳에 중복되면 어긋난다.

## 작업

### 1. `frontend/src/components/UploadDropzone.tsx` + `UploadDropzone.test.tsx`

```tsx
export function UploadDropzone({ onUploaded }: { onUploaded: () => void }): React.ReactElement;
```

- Client Component
- 드래그&드롭 영역 + 클릭하면 열리는 파일 선택(`<input type="file">`는 시각적으로 숨기고 label로 연결). 드래그 오버 상태는 **테두리 색 변화 정도로만** 표현한다(글로우·스케일 애니메이션 금지)
- 함께 입력받는 것: 제목(선택 — 비우면 백엔드가 파일명에서 정한다), 태그(쉼표로 구분해 배열로 변환, 공백 제거, 빈 항목 제외), 공개범위(`공개`/`비공개` — 값은 `public`/`private`)
- 허용 형식 안내를 한 줄 표시한다. 목록은 `SUPPORTED_CONTENT_TYPES`에서 만들고 하드코딩하지 마라
- **원본 파일을 보관하지 않는다는 사실을 한 줄로 알린다.** 예: "업로드한 파일에서 텍스트만 추출해 저장합니다. 원본 파일은 보관하지 않습니다." — `text-neutral-500`. 이유: ADR-017. 나중에 "원본 다운로드"를 찾는 오해를 입구에서 차단한다
- 업로드 중에는 입력과 버튼을 비활성화하고 "업로드 중…" 정도의 텍스트를 둔다(스피너 애니메이션 금지)
- 성공 시: 폼을 초기화하고 `onUploaded()`를 호출한다. **성공 문구에 "임베딩 완료"라고 쓰지 마라** — 이 시점의 상태는 `pending`이다. "업로드했습니다. 임베딩이 끝나면 상태가 완료로 바뀝니다." 정도로 쓴다
- 실패 시: `ApiError.detail`을 그대로 노출한다. 이 메시지만 빨간색(`#ef4444`)을 쓴다

**익명 상태 처리** — `getCurrentUser()`가 `null`이면 업로드할 수 없다(백엔드가 400을 준다). 요청을 보내 실패를 보여주지 말고, **미리 비활성화하고 안내한다**: "사용자를 선택하면 업로드할 수 있습니다." 이유: 실패할 것이 확실한 요청을 보내는 것은 사용자에게 불필요한 오류를 보여주는 것이다.

테스트(`vi.stubGlobal("fetch", ...)`)에서 최소한 검증할 것:
- 파일을 고르고 제출하면 `POST /api/documents`가 `FormData`로 불린다
- 태그 문자열 `"규정, 인사"`가 태그 2개로 전송된다
- 400 응답의 `detail`이 화면에 그대로 나온다
- 성공 시 `onUploaded`가 불린다
- 익명이면 제출 버튼이 비활성화되고 안내 문구가 보인다

### 2. `frontend/src/app/page.tsx`에 조립

- 드롭존을 목록 **위**에 둔다
- `onUploaded`에 step 2의 `useDocuments().refresh`를 연결한다. 이유: 업로드 직후 폴링 주기(2초)를 기다리지 않고 바로 `대기 중` 행이 나타나야 파이프라인이 도는 장면이 이어진다
- **page.tsx에는 조립만 둔다.** 업로드 로직·상태를 여기에 인라인하지 마라(`tdd-guard.sh`가 `page.tsx`를 테스트 없이 통과시킨다)

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
cd frontend && ! grep -rn "항상 최신\|실시간 동기화\|무중단\|원문" src/
```

## 검증 절차

1. 위 AC 커맨드를 전부 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 지원 형식 목록과 에러 메시지를 프론트에서 새로 만들지 않았는가?
   - 익명 상태에서 업로드 요청을 보내지 않는가?
   - 업로드 성공 문구가 임베딩 완료를 뜻하지 않는가?
   - 원본 파일을 보관하지 않는다는 안내가 있는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m3-frontend/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **클라이언트에서 파일 크기·내용을 검사해 막지 마라.** 확장자 안내는 하되, 판정은 백엔드가 한다. 이유: 판정 기준이 두 곳으로 갈리면 어긋난다
- **업로드 후 임베딩 완료를 기다리는 로딩 상태를 만들지 마라.** 이유: 임베딩은 워커가 비동기로 처리하며, 사용자는 목록의 상태 배지로 진행을 본다. 완료까지 붙잡아 두면 "DB 트리거가 파이프라인을 돌린다"는 구조가 화면에서 사라진다
- **여러 파일 동시 업로드·업로드 큐·진행률 바를 만들지 마라.** 이유: 요청받지 않은 기능이다. 한 번에 한 파일이면 데모 요건을 충족한다
- **`embedding_jobs`나 임베딩 관련 API를 프론트에서 호출하지 마라.** 이유: 잡 생성은 DB 트리거의 책임이다(CLAUDE.md CRITICAL)
- **"원문"이라는 단어를 쓰지 마라.** "추출 텍스트"로 쓴다(UI_GUIDE 용어 규칙)
- 기존 테스트를 깨뜨리지 마라
