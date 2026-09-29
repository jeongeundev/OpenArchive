# Step 6: extract-ui

이 phase(#135)는 이미지·스캔 PDF를 워커 잡으로 OCR한다(**ADR-052** — 먼저 읽어라). 백엔드는 step 1~5로 끝났다.
이 step은 **프론트엔드만** 다룬다: 이미지 업로드 허용, 추출 상태 표시, 추출 중 편집 차단.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-052**, ADR-017·ADR-035(원본 파일 / 문서 텍스트 / 추출 텍스트 용어)
- `/docs/UI_GUIDE.md` — 배지·문구 규칙 (CLAUDE.md: "항상 최신"·"실시간 동기화" 금지)
- `phases/m19-ocr/index.json` — step 1~5 summary(응답 필드 `extraction_status`, 409 문구, `/admin/status` 새 카운트)
- `frontend/src/lib/types.ts` — `SUPPORTED_CONTENT_TYPES`·`DocumentSummary`·시스템 상태 타입
- `frontend/src/components/DocumentTable.tsx`·`DocumentMeta.tsx`·`DocumentActions.tsx`·`TextEditor.tsx`·`OriginalFiles.tsx`·
  `UploadDropzone.tsx`·`StatusPanel.tsx`·`ErrorDocuments.tsx`
- `frontend/src/lib/useDocument.ts` — 상세 폴링 조건(지금은 `embedding_status`가 pending·processing일 때만)
- `frontend/src/lib/zip.ts` — ZIP 안 지원 문서 판별(`SUPPORTED_CONTENT_TYPES` 재사용)
- 각 컴포넌트의 `*.test.tsx`

## 작업

### 1) 테스트 먼저 (vitest, 기존 테스트 파일 옆에)

1. `SUPPORTED_CONTENT_TYPES`에 `png`·`jpg`·`jpeg` — 업로드·원본 교체 `accept`와 ZIP 판별이 이미지를 받는다.
2. `extraction_status === "pending"`이면 목록·상세 배지가 「텍스트 인식 중」, `"failed"`면 「텍스트 인식 실패」.
   추출 배지가 있을 때는 임베딩 배지보다 우선한다(추출 중 문서의 `embedding_status`는 의미가 없다).
3. 추출 중에는 텍스트 편집·버전 복원·다시 추출·원본 교체 버튼이 비활성이고 이유 문구가 보인다. 태그 수정·삭제는 가능하다.
4. 빈 본문 인식 실패 문서의 상세는 편집기 대신 안내("원본을 교체하거나 다시 추출해 보세요")를 보인다.
5. 상세 화면은 `extraction_status === "pending"`일 때도 폴링한다 — 인식이 끝나면 새로고침 없이 텍스트가 나타난다.
6. 상태 패널이 `/admin/status`의 `extraction_pending`·`extraction_failed`를 표시한다.

### 2) 구현

- 문구는 한국어, UI_GUIDE의 배지 색·톤 규칙을 따른다. OCR 결과도 "추출 텍스트"다(ADR-035) — 원본 파일이 있는 문서이므로
  "추출"이라는 말을 써도 된다. 인식 중 안내에 완료 시각을 약속하는 문구("곧"·"즉시"·"실시간")를 쓰지 않는다.
- 정적 빌드를 갱신한다: `cd frontend && npm run build:static` — `backend/app/static/`의 변경이 이 step의 산출물에 포함된다
  (`scripts/check.sh`도 같은 명령을 돈다).

## Acceptance Criteria

```bash
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build:static
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: TypeScript strict 위반 없음, 새 문구가 UI_GUIDE·CLAUDE.md 금지 표현을 쓰지 않는가, "원문"이라는 말로 뭉뚱그리지 않았는가.
3. `phases/m19-ocr/index.json`의 step 6을 갱신한다.

## 금지사항

- 백엔드 코드를 고치지 마라. 이유: 이 step은 frontend 스코프다. 응답 필드가 부족하면 blocked로 멈추고 사유를 적는다.
- 추출 중 문서를 목록에서 숨기지 마라. 이유: 업로드 즉시 문서가 보이는 것이 ADR-052의 선택 이유다.
- `RequireAuth`가 이미 막는 조건을 페이지 안에서 다시 검사하지 마라. 이유: 도달 불가 가드가 두 번 결함으로 잡혔다(m11-a·#78).
- 기존 테스트를 깨뜨리지 마라
