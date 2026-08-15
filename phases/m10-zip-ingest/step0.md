# Step 0: zip-expand

## 배경 — ZIP은 브라우저에서 푼다. 서버는 단건 API 그대로다

문서를 하나씩 올리는 흐름은 이미 다중 파일까지 확장돼 있다
(`frontend/src/components/UploadDropzone.tsx` — 파일별 순차 POST, 파일별 상태 행). 남은 것은
**ZIP 하나를 끌어놓으면 그 안의 문서들이 같은 흐름으로 들어가는 것**이다.

**서버는 손대지 않는다.** ZIP 해제는 브라우저에서 하고, 나온 파일은 기존
`POST /api/documents`에 한 건씩 보낸다. 이유는 ADR-017이다 — 이 플랫폼은 **원본 파일을
보관하지 않는다.** 서버가 ZIP을 받으면 압축 아카이브라는 원본 파일을 서버에서 다루게 되고,
zip bomb·경로 탈출(`../`)·인코딩 같은 문제를 서버가 떠안는다. 브라우저에서 풀면 서버 API·스키마
변경이 0이고, 사용자의 기기 밖으로 나가는 것은 지금과 똑같이 개별 문서의 텍스트뿐이다.

이 step은 **해제 모듈 하나**만 만든다. 화면 연결은 step 1이다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

### 결정 1 — 라이브러리는 JSZip이다

MIT 라이선스이며 브라우저에서 동작한다. 대회 규정상 라이선스가 중요하므로 다른 것으로
바꾸지 마라. `frontend/package.json`의 `dependencies`에 추가한다(현재 `next`·`react`·
`react-dom` 셋뿐이다 — 네 번째가 된다).

### 결정 2 — 모듈은 순수 함수다. React를 import하지 않는다

`src/lib/` 아래에 둔다. 이 디렉토리는 API 클라이언트·훅·타입이 사는 곳이고, `relations.ts`처럼
순수 로직 모듈의 선례가 있다. 컴포넌트를 알지 못해야 step 1이 자유롭게 붙일 수 있고,
테스트도 DOM 없이 돌아간다.

### 결정 3 — 걸러낸 항목은 버리지 않고 이름을 돌려준다

미지원 항목을 조용히 무시하면 사용자는 20개를 넣고 12개만 올라간 이유를 알 수 없다.
해제 결과는 **넣을 파일**과 **건너뛴 이름** 두 가지를 함께 반환한다. 화면에 어떻게 보일지는
step 1이 정한다.

### 결정 4 — 중첩 ZIP은 풀지 않는다

ZIP 안의 `.zip`은 지원 확장자가 아니므로 자연히 "건너뜀"이 된다. 재귀 해제를 만들지 마라 —
요청받지 않았고, 깊이 제한·순환 같은 판단이 따라온다.

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `frontend/src/lib/types.ts` — `SUPPORTED_CONTENT_TYPES`(:185)가 지원 형식의 정본이다.
  **이 값을 복제하지 말고 import해서 써라**
- `frontend/src/lib/relations.ts`와 `relations.test.ts` — 순수 로직 모듈과 그 테스트의 본보기.
  파일 배치·export 방식·테스트 서술 톤을 여기에 맞춰라
- `frontend/package.json` — 의존성과 스크립트(`test`는 `vitest run`이다)
- `frontend/vitest.config.ts` — `environment: "jsdom"`. 브라우저 API(`File`·`Blob`)를 테스트에서
  쓸 수 있다
- `frontend/src/components/UploadDropzone.tsx` — **읽되 고치지 마라**(step 1의 대상).
  `UploadItem` 타입과 `UploadItemStatus`에 이미 `"건너뜀"`이 들어 있는 것을 확인하라.
  이 모듈의 반환 형태는 그 타입에 자연스럽게 얹힐 수 있어야 한다
- `docs/ADR.md` 의 **ADR-017**(원본 파일 미보관 — 서버 ZIP을 두지 않는 근거)
- `docs/UI_GUIDE.md` — 이 step은 화면을 만들지 않지만, 다음 step의 제약을 알고 있어야
  반환 형태를 잘못 잡지 않는다

## 작업

### 1) JSZip을 추가한다

```bash
cd frontend && npm install jszip
```

타입 정의가 패키지에 포함되어 있는지 확인하라. 별도 `@types` 패키지가 필요하면
`devDependencies`에 넣는다. `package-lock.json`도 함께 커밋되어야 한다.

### 2) 테스트를 먼저 쓴다 — `frontend/src/lib/zip.test.ts`

테스트용 ZIP은 JSZip으로 직접 만든다(`new JSZip()`에 항목을 넣고
`generateAsync({ type: "blob" })`). 검증 대상은 ZIP 포맷 자체가 아니라 **골라내는 규칙**이므로
이 방식으로 충분하다.

- 지원 확장자(`pdf`·`docx`·`txt`·`md`)만 파일로 나오고, 나머지(`png`·`zip`·확장자 없음)는
  건너뛴 이름에 들어간다
- 디렉터리 항목은 파일로도 건너뜀으로도 나오지 않는다 — 사용자가 넣은 것이 아니라 구조다
- macOS가 만드는 `__MACOSX/` 아래 항목과 `.DS_Store`, 그 밖에 `.`으로 시작하는 숨김 파일은
  제외한다. **이것들은 건너뜀 목록에도 넣지 마라** — 사용자가 존재를 모르는 항목이라
  "12개를 건너뛰었습니다"의 대부분을 채워 진짜 이유를 가린다
- 중첩 디렉터리 안의 파일은 **basename만** 파일명이 된다 (`docs/guide.md` → `guide.md`).
  경로가 파일명에 남으면 서버가 그것을 원본 파일명으로 저장한다
- 대문자 확장자(`GUIDE.MD`)도 지원 형식으로 인식한다
- 손상된 ZIP(임의 바이트)은 예외를 던진다. **조용히 빈 결과를 반환하지 마라** —
  사용자는 파일이 비었는지 깨졌는지 알아야 한다
- 반환된 항목이 실제 `File`이며 내용이 보존된다 (`text()`로 확인)

**이 시점에 실행하면 실패한다. 그게 정상이다.**

### 3) `frontend/src/lib/zip.ts`를 만든다

시그니처만 제시한다. 내부 구현은 재량이다.

```typescript
export type ExpandedZip = {
  files: File[];
  skipped: string[];
};

export async function expandZip(archive: File): Promise<ExpandedZip>;
```

- 지원 확장자 판정은 `SUPPORTED_CONTENT_TYPES`를 기준으로 한다. 목록을 손으로 다시 적지 마라
- 반환하는 `File`의 `name`은 basename이다
- 정렬 순서를 보장할 필요는 없지만, JSZip이 주는 순서를 임의로 흐트러뜨리지도 마라

## Acceptance Criteria

```bash
cd frontend

# 1) 새 모듈
npm run test -- src/lib/zip.test.ts
#   → 전부 passed

# 2) 기존 테스트 회귀 없음 (특히 업로드 화면 11케이스)
npm run test
#   → 전부 passed

# 3) 린트와 빌드
npm run lint && npm run build
#   → 에러 없음

# 4) 화면과 백엔드를 건드리지 않았다
cd .. && git diff --stat HEAD -- frontend/src/components/ frontend/src/app/ backend/
#   → 출력이 비어 있어야 한다

# 5) 전체 검증
bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `zip.ts`가 React나 컴포넌트를 import하지 않는가? (결정 2)
   - `SUPPORTED_CONTENT_TYPES`를 복제하지 않고 import했는가?
   - 백엔드가 변경 0줄인가? 이 phase는 서버 API·스키마를 바꾸지 않는다 (AC 4번이 잡는다)
   - `package.json`에 JSZip 외의 의존성이 늘지 않았는가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 2에서 일괄 처리).
4. 결과에 따라 `phases/m10-zip-ingest/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **서버에 ZIP 업로드 경로를 만들지 마라.** 이유: ADR-017. 원본 파일을 보관하지 않는 제품에서
  서버가 아카이브를 받으면 zip bomb·경로 탈출을 서버가 떠안고, 이 phase가 지키는 "스키마 변경
  0 · 서버 API 변경 0"이 깨진다.
- **중첩 ZIP을 재귀로 풀지 마라.** 이유: 결정 4. 깊이 제한과 순환 처리가 따라온다.
- **`__MACOSX`·숨김 파일을 건너뜀 목록에 넣지 마라.** 이유: 사용자가 만든 적 없는 항목이
  목록을 채우면 진짜 건너뛴 문서가 묻힌다.
- **파일 크기 검사를 이 모듈에 넣지 마라.** 이유: 10MB 선검사는 `UploadDropzone`에 이미 있고
  (`MAX_UPLOAD_BYTES`), 경계의 최종 권위는 백엔드의 413이다. 같은 검사를 세 곳에 두지 않는다.
- **`UploadDropzone.tsx`를 고치지 마라.** 이유: step 1의 작업이다.
- **기존 테스트를 깨뜨리지 마라.** 특히 `UploadDropzone.test.tsx`의 11개 케이스는
  이 phase 전체에서 **무수정 통과**가 목표다.
