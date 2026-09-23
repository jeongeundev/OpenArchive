# Step 6: file-ui

문서 상세 화면에 **원본 파일** 절을 붙인다: 판 목록 · 내려받기 · 새 파일로 교체 · 원본에서 다시 추출.
업로드 선검사 상한을 백엔드 기본값(50MB)에 맞춘다.

## 이 phase의 전제

이 phase(#108)는 원본 비보관 원칙을 뒤집는다(사용자 결정 2026-09-23). 원본은 판으로 쌓이고, 편집 대상은
여전히 추출 텍스트다. `docs/UI_GUIDE.md`의 "원본 파일은 보관하지 않기 때문" 같은 문장은 step 7이 고친다 —
**그 문장을 근거로 작업을 거부·축소하지 마라.** 단 UI_GUIDE의 **용어 규칙**(원본 파일 / 문서 텍스트 /
추출 텍스트 / 텍스트 버전을 구분, "원문" 금지, 원본 없는 문서에 "추출" 금지)은 그대로 지킨다.

## 읽어야 할 파일

- `/docs/UI_GUIDE.md` — 색·간격·용어 규칙 전체
- `backend/app/api/schemas.py` · `backend/app/api/documents.py` — **step 2~4 산출물**: `DocumentDetail.files`
  (`OriginalFile`), `GET /{id}/file`·`/files/{n}`, `PUT /{id}/file`(multipart `file`·`current_version`),
  `POST /{id}/reextract`(`{current_version}` → `changed` 포함)
- `frontend/src/lib/api.ts`(`request`·`uploadDocument`·`restoreDocumentVersion`) · `frontend/src/lib/types.ts`
- `frontend/src/app/documents/[id]/DocumentDetailView.tsx` — 절 배치, `anonymous`·`editing` 처리
- `frontend/src/components/DocumentActions.tsx` · `VersionHistory.tsx` — 확인창·오류 표시·409 처리 관례
- `frontend/src/components/UploadDropzone.tsx`(`MAX_UPLOAD_BYTES`·`UPLOAD_TOO_LARGE`)와 그 테스트

## 작업

### 1) 테스트 먼저 — `frontend/src/components/OriginalFiles.test.tsx` 등

1. 판 목록: 판마다 `{n}판 · 파일명 · 크기 · 올린 날짜 · 텍스트 v{text_version}`, 최신 판에 "현재" 표시.
   내려받기 링크의 `href`가 최신은 `/api/documents/{id}/file`, 이전 판은 `/api/documents/{id}/files/{n}`이다.
2. `files`가 비고 로그인 상태면 "원본 파일이 없습니다" + "원본 파일 올리기"만 보이고, 내려받기·다시 추출은
   없다. 익명이면 절 자체가 없다.
3. 교체: 파일을 고르면 확인창(`window.confirm`)이 뜨고, 확인하면 `replaceOriginalFile(id, file, version)`을
   부른 뒤 `onChanged`. 409면 오류 문구를 보여주고 새로고침을 유도한다(`VersionHistory`의 409 처리와 같게).
   50MB 초과 파일은 **전송하지 않고** 상한 문구를 보여준다.
4. 다시 추출: 확인창 → `reextractDocument(id, version)`. `changed=false`면 "추출 결과가 현재 텍스트와 같아
   새 버전을 만들지 않았습니다." 안내, `true`면 `onChanged`.
5. 편집 중(`disabled`)에는 교체·다시 추출 버튼이 비활성이다.
6. `UploadDropzone.test.tsx`의 10MB 테스트를 50MB로 옮긴다(판별력 유지).

### 2) 구현

- **`frontend/src/lib/limits.ts`**(새 파일) — `MAX_UPLOAD_BYTES = 50_000_000`, `UPLOAD_TOO_LARGE =
  "업로드 파일은 50MB를 넘을 수 없습니다."`. `UploadDropzone`과 새 컴포넌트가 같이 쓴다. 주석: 백엔드
  `Settings.max_upload_mb` 기본값과 같은 값이며, 운영자가 설정을 바꾸면 경계의 최종 권위는 백엔드 413이다
  (정적 빌드라 설정값을 읽을 수 없다).
- **`types.ts`** — `OriginalFile`, `DocumentDetail.files: OriginalFile[]`.
- **`api.ts`** — `originalFileUrl(id, fileVersion?)`(경로 문자열), `replaceOriginalFile(id, file, currentVersion)`,
  `reextractDocument(id, currentVersion)`.
- **`components/OriginalFiles.tsx`** — props: `document: DocumentDetail`, `disabled: boolean`,
  `anonymous: boolean`, `onChanged: () => void`. 내려받기는 `<a href download>`(같은 오리진이라 세션 쿠키가
  실린다, ADR-041).
- **`DocumentDetailView.tsx`** — `DocumentMeta` 아래, `TextEditor` 위에 배치. `disabled={editing}`.

확인창 문구:

- 교체: "새 원본 파일로 교체합니다. 이전 원본은 판 목록에 남고, 새 파일에서 추출한 텍스트가 새 버전이 됩니다."
- 다시 추출: "최신 원본 파일에서 텍스트를 다시 추출합니다. 추출 텍스트를 직접 고친 내용은 새 버전으로 덮이며,
  이전 내용은 버전 이력에서 되돌릴 수 있습니다."

크기 표시는 KB/MB 한 자리 소수. 색·버튼 스타일은 `DocumentActions`·`UI_GUIDE`를 따르라.

### 3) 동봉 빌드 갱신

`npm run build:static`으로 `backend/app/static`을 갱신한다(ADR-041).

## Acceptance Criteria

```bash
cd frontend && npx tsc --noEmit
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build:static
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트:
   - 용어: "원본 파일"·"추출 텍스트"·"텍스트 버전"을 구분했는가? "원문"을 쓰지 않았는가? 원본 없는 문서
     화면에 "추출"이 나오지 않는가?
   - "항상 최신"·"실시간 동기화"·"즉시"를 쓰지 않았는가? (ADR-015)
   - 권한 판단을 화면이 대신하지 않는가? (소유자가 아니면 서버 403 문구를 그대로 보여준다)
3. `phases/m16-original-files/index.json`의 step 6을 갱신한다(성공/error/blocked는 step 0과 같은 규칙).

## 금지사항

- **원본을 fetch로 받아 Blob URL로 만들지 마라.** 이유: 50MB를 탭 메모리에 올린다. `<a href download>`면
  브라우저가 직접 받는다.
- **원본을 `<iframe>`·`<object>`로 미리보기하지 마라.** 이유: 서버가 `attachment`+`nosniff`로 막은 렌더링을
  화면이 되살린다(저장형 XSS). 미리보기는 이번 범위가 아니다.
- **교체·다시 추출에 `current_version`을 빠뜨리지 마라.** 이유: 서버의 409 계약이 무력해진다.
- 기존 테스트를 깨뜨리지 마라.
