# Step 4: text-vocabulary-ui

## 배경 — 추출된 적 없는 텍스트에 "추출 텍스트"라고 쓰게 됐다

`ADR-017`은 프로젝트 전역 어휘 규칙을 정했다: **원본 파일 / 추출 텍스트 / 텍스트 버전**을
구분해 쓰고 "원문"으로 뭉뚱그리지 않는다. `docs/UI_GUIDE.md`는 그 규칙을 화면 레이블까지
내렸다 — *"편집 영역 레이블은 **추출 텍스트**다"*.

이번 phase에서 **원본 파일이 없는 문서**가 처음 생겼다. 텍스트 직접 공급 API로 들어온 문서와,
step 3에서 적재 경로가 바뀐 seed 문서가 그렇다(`filename IS NULL`). 이 문서를 Web UI에서 열면
편집 영역 위에 "추출 텍스트"라고 표시된다 — **무엇에서도 추출되지 않은 텍스트에 대해서.**

이 phase의 어휘 결정은 포함 관계다:

- **문서 텍스트** (`documents.content`) — 정본 명칭. 상위 개념
- **추출 텍스트** — 문서 텍스트 중 파일 업로드 경로에서 만들어진 것. `filename IS NOT NULL`
- **텍스트 버전** (`document_versions`) — 기존 그대로

화면도 그 구분을 따르게 한다. **이 step은 그것뿐이다.**

## 이전 step에서 만들어진 것

- **step 0~1** — `POST /api/documents/text`로 공급된 문서는 `filename`이 `null`이다.
  API 응답 타입은 바뀌지 않았다 — `filename`은 이전부터 `string | null`이었다
  (`frontend/src/lib/types.ts:8,44`)
- **step 3** — seed 문서도 `filename`이 `null`이 된다. 즉 시연 데이터 대부분이 이 경우다

## ✅ 이미 닫힌 결정

### 결정 1 — 판정 기준은 `filename`이지 `content_type`이 아니다

`content_type`은 텍스트 직접 공급에서도 `md`·`txt`라 업로드된 `.md` 파일과 구별되지 않는다.
원본 파일의 유무를 말하는 필드는 `filename` 하나다.

### 결정 2 — `pdf`·`docx` 추출 안내문은 손대지 않는다

`TextEditor.tsx:87-90`의 상시 안내("원본 파일이 아니라 업로드 시 추출된 텍스트를 편집합니다")는
`content_type`이 `pdf`·`docx`일 때만 뜬다. 그 문서는 정의상 파일 업로드로 들어왔으므로 문구가
정확하다. **조건이나 문구를 바꾸지 마라.**

### 결정 3 — `docs/UI_GUIDE.md`는 이 step에서 고치지 않는다

문서 반영은 step 5가 일괄로 한다. 여기서는 코드와 테스트만 바꾼다.

## 읽어야 할 파일

- `docs/UI_GUIDE.md` 「용어 규칙 — 원본 파일과 추출 텍스트를 구분한다」(:97 부근)와
  「추출 텍스트 편집」(:107 부근) — 지금 규칙의 원문
- `docs/ADR.md` 의 **ADR-017** — 어휘 구분의 근거
- `frontend/src/components/TextEditor.tsx` — **수정 대상 전체.** "추출 텍스트"가 나오는 곳은
  네 군데다: 저장 실패 문구(:64) · 섹션 제목(:74) · pdf/docx 안내(:88) · textarea의 sr-only
  레이블(:94)
- `frontend/src/components/TextEditor.test.tsx` — **깨뜨리면 안 되는 계약 전량.** 여러 테스트가
  `getByRole("textbox", { name: "추출 텍스트" })`로 요소를 찾는다. 이 파일의 fixture는
  `filename: "guide.pdf"`라 기존 테스트는 계속 "추출 텍스트"를 봐야 한다
- `frontend/src/components/DocumentMeta.tsx` — `document.filename ?? "—"` 처리. 참고만 하고
  고치지 마라
- `frontend/src/lib/types.ts` — `DocumentDetail` 타입

## 작업

### 1) 테스트를 먼저 쓴다

`frontend/src/components/TextEditor.test.tsx`에 추가한다:

- **`filename: null`인 문서**에서 섹션 제목과 textarea 레이블이 **"문서 텍스트"**다
- 같은 문서에서 pdf/docx 추출 안내가 뜨지 않는다(`content_type`이 `md`이므로 기존 조건 그대로)
- **`filename`이 있는 문서**는 여전히 "추출 텍스트"다 — 회귀 검출기다. 기존 테스트가 이미
  이 경우를 쓰고 있으니 중복이면 새로 만들지 말고 기존 테스트로 충분한지 판단하라

**이 시점에 실행하면 새 테스트가 실패한다. 그게 정상이다.**

### 2) `frontend/src/components/TextEditor.tsx`를 고친다

레이블을 한 곳에서 계산해 세 자리(섹션 제목·sr-only 레이블·저장 실패 문구)에 쓴다.

```tsx
const textLabel = document.filename === null ? "문서 텍스트" : "추출 텍스트";
```

- 세 자리에 각각 삼항 연산자를 흩뿌리지 마라 — 한 곳에서 정하고 재사용한다
- `htmlFor`/`id`의 `extracted-text`는 **그대로 둔다.** 사용자에게 보이지 않는 DOM 식별자이고,
  바꾸면 이 step의 요청과 무관한 diff가 늘어난다
- 그 밖의 마크업·스타일·상태 로직을 건드리지 마라

## Acceptance Criteria

```bash
cd frontend

# 1) 컴포넌트 테스트가 전부 통과한다 (신규 + 기존 회귀)
npx vitest run src/components/TextEditor.test.tsx
#   → 전부 passed

# 2) 프론트 전체 테스트·린트·빌드
npm run test && npm run lint && npm run build
#   → 전부 성공 (타입 에러 0)

# 3) 두 문구가 조건부로 갈린다
grep -n "문서 텍스트\|추출 텍스트" src/components/TextEditor.tsx
#   → 레이블 계산 한 줄에 두 문구가 함께 나오고, 나머지 자리는 그 변수를 쓴다.
#     pdf/docx 안내문(:88 부근 "업로드 시 추출된 텍스트")은 그대로 남아 있어야 한다

# 4) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `UI_GUIDE.md`의 다른 규칙(편집 영역 배치, 상시 안내, 버전 표기)을 바꾸지 않았는가?
   - 백엔드·API 응답 스키마를 건드리지 않았는가? (이 step은 화면 문구만 다룬다)
   - TypeScript strict mode에서 타입 에러가 없는가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 5에서 일괄 처리).
4. 결과에 따라 `phases/m11b-text-ingest/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **Web UI에 텍스트 직접 공급 화면을 만들지 마라.** 이유: 이 이슈가 여는 것은 프로그래매틱
  공급 경로다. 업로드 화면 옆에 "텍스트로 붙여넣기" 폼을 추가하는 것은 요청받지 않은 기능이다.
- **`content_type`으로 원본 파일 유무를 판정하지 마라.** 이유: 텍스트 직접 공급 문서도
  `md`·`txt`라 업로드된 파일과 구별되지 않는다 (결정 1).
- **pdf/docx 상시 안내의 조건이나 문구를 바꾸지 마라.** 이유: 그 문서는 정의상 파일
  업로드로 들어왔고, `TextEditor.test.tsx:35`가 이 동작을 고정한다.
- **`DocumentMeta.tsx`·`DocumentTable.tsx` 등 다른 컴포넌트를 고치지 마라.** 이유: 이미
  `filename ?? "—"`로 NULL을 정확히 다루고 있다. 고칠 것이 없다.
- **`docs/UI_GUIDE.md`를 고치지 마라.** 이유: step 5가 ADR·ARCHITECTURE·PRD와 함께 일괄
  반영한다. 여기서 먼저 고치면 문서 변경이 두 커밋에 흩어진다.
- **기존 테스트를 깨뜨리지 마라.**
