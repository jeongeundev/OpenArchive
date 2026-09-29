# Step 7: docs-ocr

이 phase(#135)는 이미지·스캔 PDF를 tesseract로 OCR하고, 추출을 워커 잡으로 옮겼다(**ADR-052**). 코드는 step 0~6에서
끝났다. 이 step은 **문서만** 고친다 — 코드와 문서가 어긋나지 않게 한다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-052** 전체(이 step에서 ADR 본문은 고치지 않는다 — 구현과 어긋난 점을 발견하면 summary에 적는다)
- `phases/m19-ocr/index.json` — step 0~6 summary(실제 구현된 이름·동작의 정본)
- `/docs/ARCHITECTURE.md` — 스키마·트리거·워커 잡 종류·업로드 흐름 절
- `/docs/PRD.md` — 지원 형식·「하지 않는 것」
- `/docs/UI_GUIDE.md` — 배지 목록
- `/docs/ROADMAP.md` — 파서 표, `| 스캔 PDF OCR | 장기 | …` 행
- `/docs/OPERATIONS.md` — 설치·오프라인 운영 절
- `/docs/SETUP_OPENSQL.md` — Rocky Linux 앱 호스트 준비가 있으면 그 절
- `README.md` — 지원 형식·시작하기
- `backend/migrations/021_extract_tables.sql`·`022_extract_triggers.sql`, `backend/app/worker.py`, `backend/app/services/parsing.py`

## 작업

1. **ARCHITECTURE.md**: `extraction_status`와 조건부 빈 본문 제약, 추출 잡(`kind='extract'`) — 트리거가 만든다, 워커가
   최신 원본 판을 OCR해 `apply_extracted_text`로 반영하면 003 트리거가 텍스트 버전·임베딩 잡을 잇는다. 잡 종류를 "두 종류"라고
   적은 곳을 모두 세 종류로 고친다(`grep -n "두 종류" docs/ARCHITECTURE.md`).
2. **PRD.md**: 지원 형식에 png·jpg·jpeg와 스캔 PDF(OCR). 텍스트가 일부 쪽에만 있는 PDF는 OCR하지 않는다는 한계(ADR-052 트레이드오프 1).
3. **UI_GUIDE.md**: 「텍스트 인식 중」·「텍스트 인식 실패」 배지와 추출 중 비활성 규칙.
4. **ROADMAP.md**: 스캔 PDF OCR 행을 완료로 옮기고, 쪽 단위 혼합 추출(OCRmyPDF `--skip-text` 방식)을 후속 후보로 남긴다. 파서 표에 이미지 형식.
5. **OPERATIONS.md**: OCR 엔진 설치 — Rocky 9 `dnf install tesseract tesseract-langpack-kor`(AppStream, 4.1.1), Ubuntu
   `apt install tesseract-ocr tesseract-ocr-kor`, macOS `brew install tesseract tesseract-lang`. 설치 확인 `tesseract --list-langs`에
   `kor`. 오프라인: 모델은 패키지 안 파일(`kor.traineddata`) 하나라 인터넷이 필요 없다. OCR 대기·실패 확인은 `/admin/status`,
   실패 문서는 원본 교체나 `openarchive reextract`로 다시 돌린다.
6. **README.md**: 지원 형식 목록과 필요 시스템 패키지 한 줄.
7. `grep -rn "스캔 이미지 PDF\|스캔 PDF" docs README.md`로 "미지원·거부"라고 적힌 문장을 모두 찾아 현재 동작으로 고친다.

## Acceptance Criteria

```bash
grep -rn "extraction_status" docs/ARCHITECTURE.md
grep -rn "tesseract-langpack-kor" docs/OPERATIONS.md
grep -rn "텍스트 인식 중" docs/UI_GUIDE.md
! grep -rn "스캔 이미지 PDF는 지원하지 않" docs README.md
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: "항상 최신"·"실시간 동기화" 같은 금지 표현이 없는가? "원문"으로 뭉뚱그리지 않았는가(ADR-017·035)?
   수치(CER·쪽당 시간)는 ADR-052·#135 코멘트와 같은가? **VM(Rocky 9, tesseract 4.1.1) 정확도는 미검증**이라고 적었는가
   (재지 않은 것을 잰 것처럼 쓰지 마라)?
3. `phases/m19-ocr/index.json`의 step 7을 갱신한다.

## 금지사항

- 코드·마이그레이션·테스트를 고치지 마라. 이유: docs 스코프다. 문서와 코드가 어긋나면 문서를 코드에 맞추고, 코드가 틀려 보이면 summary에 적는다.
- ADR-052 본문을 고치지 마라. 이유: 결정 기록은 사람이 검토해 고친다.
- `backend/migrations/002_tables.sql` 등 적용된 마이그레이션의 주석을 고치지 마라. 이유: 적용 이력 파일이다 — 021 머리 주석이 대체한다.
- 기존 테스트를 깨뜨리지 마라
