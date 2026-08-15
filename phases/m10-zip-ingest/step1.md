# Step 1: zip-dropzone

## 배경 — 화면은 이미 다중 파일을 다룬다. ZIP은 그 입구에 붙는다

`frontend/src/components/UploadDropzone.tsx`는 이미 여러 파일을 받아 **한 건씩 순차로**
`POST /api/documents`에 보내고, 파일별 상태 행을 표시한다. 그 구조에 필요한 자리가 이미
뚫려 있다:

```typescript
type UploadItemStatus = "대기" | "업로드 중" | "완료" | "실패" | "건너뜀";

type UploadItem = {
  file?: File;          // ← optional이다
  name: string;         // ← 파일과 별개로 이름을 갖는다
  status: UploadItemStatus;
  detail?: string;
};
```

`file`이 optional이고 `name`이 따로 있으며 `"건너뜀"` 상태가 존재한다. **ZIP 안의 미지원
항목을 `file` 없이 이름만으로 목록에 표시하라는 뜻이다.** `fileCount`가
`item.file !== undefined`인 것만 세는 것도 같은 이유다 — 건너뛴 항목은 업로드 대상 수에
들어가지 않는다.

step 0이 만든 `expandZip`이 `{ files, skipped }`를 준다. 이 step은 그것을 위 타입에
얹기만 한다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

### 결정 1 — 기존 타입에 얹는다. 새 상태값·새 타입을 만들지 마라

`UploadItemStatus`에 `"압축 해제 중"` 같은 값을 추가하지 마라. 해제는 사용자가 파일을
**선택하는 순간** 일어나고 곧 끝난다. 목록에 나타나는 시점에는 이미 `대기`이거나 `건너뜀`이다.

### 결정 2 — 해제 시점은 선택 직후다. 업로드 버튼을 누를 때가 아니다

사용자가 ZIP을 고르면 즉시 풀어서 **무엇이 올라가고 무엇이 건너뛰어지는지 보여준다.**
업로드를 누른 뒤에 목록이 바뀌면 사용자가 확인한 것과 실제로 보낸 것이 달라진다.

### 결정 3 — ZIP과 일반 파일을 함께 선택할 수 있다

`selectFiles`는 이미 `File[]`을 받는다. 그중 `.zip`인 것만 풀고 나머지는 그대로 둔다.
"ZIP은 단독으로만" 같은 제약을 만들지 마라 — 구현이 더 늘고 사용자에게 이유를 설명할 수 없다.

### 결정 4 — 프로그레스 바를 만들지 마라

`docs/UI_GUIDE.md` "애니메이션" 절이 fade-in 하나만 허용하고 나머지를 전부 금지한다.
진행 표시는 **지금과 같은 텍스트 상태 행**(`파일명 — 업로드 중`)으로 충분하다.

### 결정 5 — ZIP은 서버로 보내지 않는다

`.zip`은 `SUPPORTED_CONTENT_TYPES`에 없으므로 서버가 415로 거부한다. 해제 결과만 보낸다.
`accept` 속성에는 `.zip`을 넣되, **업로드 대상 목록에는 ZIP 자신이 들어가지 않는다.**

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `frontend/src/components/UploadDropzone.tsx` — **이 step의 유일한 주 수정 대상.**
  전체를 읽어라. 특히 `UploadItem`/`UploadItemStatus` 타입, `selectFiles`,
  `fileCount`/`pendingCount`, `submit`의 순차 루프, `accept` 속성이 만들어지는 자리
- `frontend/src/components/UploadDropzone.test.tsx` — **기존 11개 케이스.** 전부 읽어라.
  이 step의 성공 조건 하나가 **이 케이스들의 무수정 통과**다. 특히
  "여러 파일을 선택하면 제목 입력을 숨긴다"와 "10MB를 넘는 파일은 전송하지 않고 실패로
  표시한다"가 `fileCount` 계산에 걸려 있다
- `frontend/src/lib/zip.ts`와 `zip.test.ts` — step 0의 산출물. `expandZip`의 반환 형태와
  어떤 항목이 `skipped`에 들어가고 어떤 것이 아예 빠지는지 확인하라
- `frontend/src/lib/types.ts` — `SUPPORTED_CONTENT_TYPES`
- `docs/UI_GUIDE.md` — 애니메이션 절(:196), 유사 문서 표시 절(:160 — **중복 이유로 업로드를
  막지 않는다**)

## 작업

### 1) 테스트를 먼저 쓴다 — `frontend/src/components/UploadDropzone.test.tsx`

기존 11개 케이스에 **추가**한다. 기존 케이스를 수정하지 마라.

- ZIP을 선택하면 그 안의 지원 문서마다 `POST /api/documents`가 한 번씩 발생하고,
  전송되는 파일명이 basename이다
- ZIP 안의 미지원 항목이 목록에 `이름 — 건너뜀`으로 표시되고, **그 항목으로는 요청이
  나가지 않는다**
- ZIP과 일반 파일을 함께 선택하면 양쪽 모두 업로드된다 (결정 3)
- 태그와 공개범위가 ZIP에서 나온 모든 요청에 실린다 (기존 다중 파일 케이스와 같은 계약)
- ZIP에서 파일이 2개 이상 나오면 **제목 입력이 숨는다** — 기존 규칙(`fileCount <= 1`)이
  해제 결과에도 적용된다는 것을 고정한다
- 손상된 ZIP을 선택하면 사용자에게 보이는 오류 문구가 뜨고, 요청이 나가지 않는다
- 드롭으로도 ZIP을 넣을 수 있다

테스트용 ZIP은 step 0의 테스트와 같은 방식(JSZip으로 생성)으로 만든다. `expandZip`을
모킹하지 마라 — 화면과 해제 모듈이 실제로 맞물리는지가 이 step의 검증 대상이다.

**이 시점에 실행하면 실패한다. 그게 정상이다.**

### 2) `UploadDropzone.tsx`를 고친다

- `selectFiles`를 비동기로 바꾸거나, 그 안에서 ZIP만 골라 `expandZip`을 부른다.
  해제 중 오류는 기존 `setError` 경로로 보여준다
- 해제 결과의 `files`는 `{ file, name: file.name, status: "대기" }`로,
  `skipped`는 `{ name, status: "건너뜀" }`로 목록에 넣는다 (결정 1 — `file` 없이)
- `accept` 속성에 `.zip`을 더한다. 지금은 `SUPPORTED_CONTENT_TYPES`에서 만들어지므로,
  **`SUPPORTED_CONTENT_TYPES` 자체에 `zip`을 넣지 마라** — 그 상수는 서버가 받는 형식의
  정본이고 서버는 ZIP을 받지 않는다
- 드롭존 안내 문구에 ZIP을 넣을 수 있다는 것을 한 줄로 알린다
- `submit`의 순차 업로드 루프는 **건드리지 마라.** 이미 `item.status !== "대기"`인 항목을
  건너뛰므로 `"건너뜀"` 항목이 자동으로 제외된다

## Acceptance Criteria

```bash
cd frontend

# 1) 업로드 화면 — 기존 11케이스 + 새 케이스
npm run test -- src/components/UploadDropzone.test.tsx
#   → 전부 passed

# 2) 기존 테스트 회귀 없음
npm run test
#   → 전부 passed

# 3) 린트와 빌드
npm run lint && npm run build
#   → 에러 없음

# 4) 기존 11케이스가 무수정으로 통과했다 — 이 phase의 성공 조건
cd .. && git diff HEAD -- frontend/src/components/UploadDropzone.test.tsx \
  | grep "^-" | grep -v "^---" | wc -l
#   → 0이어야 한다 (추가만 있고 삭제된 줄이 없다)
#     grep을 그냥 파이프하면 "일치 없음"이 exit 1이라 실패로 오해된다. wc -l로 세라.

# 5) 백엔드와 서버 API를 건드리지 않았다
git diff --stat HEAD -- backend/
#   → 출력이 비어 있어야 한다

# 6) 전체 검증
bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. AC 4번이 삭제 줄을 보고했다면, **테스트를 고쳐서 통과시킨 것이 아닌지 확인하라.**
   기존 케이스를 고쳐야만 통과한다면 그것은 구현이 기존 계약을 깬 것이다. 계약을 바꿀 근거가
   있다고 판단되면 `blocked`로 중단하고 사유를 적어라.
3. 아키텍처 체크리스트를 확인한다:
   - `UploadItemStatus`에 새 상태값을 추가하지 않았는가? (결정 1)
   - 프로그레스 바·스피너·애니메이션을 만들지 않았는가? (결정 4, UI_GUIDE)
   - `SUPPORTED_CONTENT_TYPES`에 `zip`을 넣지 않았는가?
   - 서버로 `.zip` 파일이 전송되지 않는가?
   - 중복 파일명을 이유로 업로드를 막지 않는가? (UI_GUIDE :169 — 중복을 이유로 막지 않는다)
4. `docs/` 문서는 이 step에서 고치지 않는다 (step 2에서 일괄 처리).
5. 결과에 따라 `phases/m10-zip-ingest/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **기존 11개 테스트 케이스를 수정·삭제하지 마라.** 이유: 그것들이 고정하는 계약(순차 전송,
  실패 지속, `onUploaded` 조건, 제목 숨김, 10MB 선검사)은 이 step에서 바뀌지 않는다.
  AC 4번이 이것을 검사한다.
- **프로그레스 바나 스피너를 만들지 마라.** 이유: `docs/UI_GUIDE.md`가 fade-in 외 모든
  애니메이션을 금지한다.
- **업로드를 병렬화하지 마라.** 이유: 기존 테스트 "앞 파일 업로드가 끝나기 전에는 다음 파일을
  전송하지 않는다"가 순차를 고정하고 있다. ZIP에서 파일이 늘어난다고 바꿀 이유가 아니다.
- **파일 개수 상한을 넣지 마라.** 이유: 요청받지 않았고, 어떤 값이 맞는지 근거가 없다.
- **`SUPPORTED_CONTENT_TYPES`에 `zip`을 추가하지 마라.** 이유: 그 상수는 서버가 받는 형식의
  정본이며 백엔드의 `SUPPORTED_CONTENT_TYPES`와 짝을 이룬다. 서버는 ZIP을 받지 않는다.
- **백엔드를 고치지 마라.** 이유: 이 phase는 서버 API·스키마 변경 0이 전제다.
- **기존 테스트를 깨뜨리지 마라.**
