# Step 1: ocr-parser

이 phase(#135)는 이미지와 스캔 PDF를 OCR로 추출한다. 결정은 **ADR-052**(`/docs/ADR.md` 맨 끝)에 있다 — 반드시
먼저 읽어라. 이 step은 **순수 파서 계층만** 다룬다: OCR 함수, OCR 대상 판정, 지원 형식 추가. OCR을 누가
언제 부르는지(워커 잡)는 뒤 step이 한다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-052** 전체 (결정 1·2가 이 step의 근거)
- `backend/app/services/parsing.py` — 현재 파서. 모듈 docstring의 "순수 함수" 원칙과 `extract_text`의 예외 규약
- `backend/tests/test_parsing.py` — 기존 파서 테스트 형식
- `backend/tests/fixtures/SOURCE.md` — 새 픽스처 `scan_tax_page1.jpg`·`scan_tax_pages.pdf`와 정답 `.txt`의 출처
- `backend/pyproject.toml` — 의존성 주석 형식
- `backend/tests/test_documents_api.py` — "지원하지 않는 파일 형식" 메시지에 지원 목록을 단언하는 테스트가 있다
  (`"pdf, docx, txt, md, hwp, hwpx, xlsx, pptx"` 문자열을 grep하라)

## 전제 — 로컬 환경

`tesseract`와 `kor` 모델이 설치돼 있어야 한다(`tesseract --list-langs`에 `kor`). 없으면 설치하지 말고
**blocked**로 멈춰라(`blocked_reason`: "tesseract kor 모델 없음 — `brew install tesseract tesseract-lang` 필요").

## 작업

### 1) 테스트 먼저 (`backend/tests/test_parsing.py`)

1. `test_ocr_reads_a_scanned_image` — `fixtures/scan_tax_page1.jpg`를 `ocr_text(data, "jpg")`로 읽으면
   정답 `scan_tax_page1.txt`와 **공백을 모두 지우고 NFKC 정규화한 뒤** `difflib.SequenceMatcher(None, a, b,
   autojunk=False).ratio() >= 0.85`이고, 정규화한 결과에 `"국세행정개혁위원회"`가 들어 있다.
   (실측: tessdata best/fast/std 모두 0.90~0.945. 임계 0.85는 CI 패키지 모델 차이를 견디는 값이다.)
   **구절 검사도 반드시 공백을 지운 텍스트로 한다** — tessdata std(legacy 계열) 모델은 한글 음절 사이에 공백을
   넣어 출력한다(실측: 같은 쪽이 1,141자 vs 1,684자). 원문 그대로 `in`으로 찾으면 모델에 따라 거짓 실패한다.
2. `test_ocr_reads_every_page_of_a_scanned_pdf` — `scan_tax_pages.pdf`(이미지만 든 2쪽 PDF)를
   `ocr_text(data, "pdf")`로 읽으면 정답 `scan_tax_pages.txt`와 같은 비교로 `>= 0.85`이고, 1쪽 구절
   `"국세행정개혁위원회"`가 2쪽에만 있는 구절 `"소상공인"`보다 앞에 온다(쪽 순서 — 세 모델 모두 인식하는 구절로 골랐다). 쪽 사이 구분은 `"\n\n"`이다.
3. `test_extract_text_does_not_ocr` — `extract_text(jpg 바이트, "jpg")`는 `""`, `extract_text(scan pdf, "pdf")`의
   `.strip()`은 `""`. **요청 안에서는 OCR하지 않는다**는 계약을 고정한다.
4. `test_needs_ocr` — 이미지 3형식은 텍스트와 무관하게 True, `pdf`는 추출 텍스트가 공백뿐일 때만 True,
   그 밖의 형식(`docx`·`hwp`·`txt` 등)은 빈 텍스트여도 False(그들은 기존대로 빈 추출 거부 대상이다).
5. `test_ocr_text_rejects_corrupt_image` — 깨진 바이트는 `ValueError`(메시지에 형식 이름).
6. `test_ocr_respects_exif_orientation` — `scan_tax_page1.jpg`를 90° 돌려 저장하고 EXIF Orientation 태그로
   바로 세우게 한 이미지(Pillow로 테스트 안에서 생성)도 1번과 같은 임계를 넘는다. 휴대폰 촬영본이 이 형태다.
7. 텍스트 레이어가 있는 기존 PDF 테스트가 그대로 통과하는지 확인한다(수정하지 않는다).
8. `detect_content_type("scan.JPG") == "jpg"`, `"jpeg"`·`"png"` 포함. `media_type_for`는 `image/png`·`image/jpeg`.

### 2) 구현 (`backend/app/services/parsing.py`)

```python
SUPPORTED_CONTENT_TYPES  # 끝에 "png", "jpg", "jpeg" 추가
IMAGE_CONTENT_TYPES: tuple[str, ...] = ("png", "jpg", "jpeg")
MEDIA_TYPES              # png → image/png, jpg·jpeg → image/jpeg

def needs_ocr(content_type: str, extracted_text: str) -> bool: ...
def ocr_text(data: bytes, content_type: str) -> str: ...
```

- `extract_text`는 이미지에 대해 `""`를 반환한다(OCR을 부르지 않는다). 호출부가 `needs_ocr`로 판정한다.
- `ocr_text`: tesseract를 `pytesseract`로 부른다. 언어 `"kor+eng"`, 설정 `"--psm 4"`. 둘 다 모듈 상수로 두고
  **왜 psm 4인지** 주석으로 남겨라 — 기본 `--psm 3`은 깨끗한 300dpi 원본에서 문단 블록을 통째로 빠뜨렸다
  (실측 1쪽 1,095자 중 879자, CER 0.35 → psm 4에서 0.07. #135 코멘트).
- 이미지는 Pillow로 열고 `ImageOps.exif_transpose`를 적용한다.
- PDF는 `pypdfium2`로 쪽마다 300dpi(`scale=300/72`) 렌더링해 — 150dpi로 낮추면 픽스처에서 best 모델이 0.842로
  떨어졌다(실측) — OCR하고 쪽 결과를 `"\n\n"`로 잇는다.
- 실패(열 수 없는 이미지·PDF, tesseract 실행 오류)는 `ValueError(f"{content_type.upper()} 파일을 읽을 수 없습니다.")`
  로 바꾼다. `extract_text`와 같은 규약이다. 단 **tesseract 바이너리나 언어 데이터가 없는 것**
  (`pytesseract.TesseractNotFoundError`, 언어 로드 실패)은 파일 문제가 아니므로 `ValueError`로 감싸지 말고
  그대로 올린다 — 워커가 재시도·오류로 기록해 운영자가 보게 한다.
- 모듈 docstring의 "스캔 이미지 PDF처럼 텍스트가 없는 파일도 … 빈 문자열을 그대로 반환한다" 문단을
  OCR 판정과 함께 갱신한다(`needs_ocr` → 워커 추출, ADR-052).
- `backend/pyproject.toml` 기본 의존성에 `pytesseract`(Apache-2.0)·`pypdfium2`(BSD-3/Apache-2.0)를 주석과 함께
  추가한다. 주석에 시스템 패키지 `tesseract`·한국어 모델이 필요하다고 적는다. 설치:
  `cd backend && .venv/bin/pip install -e ".[dev]"`.
- `test_documents_api.py`의 지원 형식 목록 문자열 단언은 새 목록(`..., pptx, png, jpg, jpeg`)으로 고친다 —
  지원 형식이 늘어난 명세 변경이다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/pip install -e ".[dev]" -q
cd backend && .venv/bin/pytest tests/test_parsing.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: `parsing.py`가 DB·네트워크를 건드리지 않는가(순수 함수 원칙)? `extract_text`가 OCR을
   부르지 않는가? 상용 API·원격 OCR을 쓰지 않았는가(ADR-003)?
3. `phases/m19-ocr/index.json`의 step 1을 갱신한다. summary에 새 공개 함수·상수 이름을 적는다.

## 금지사항

- `extract_text` 안에서 OCR을 호출하지 마라. 이유: 업로드 요청 안에서 수십 초가 걸린다 — 추출은 워커 잡이
  한다(ADR-052 결정 3).
- EasyOCR·PaddleOCR·원격 OCR API를 쓰지 마라. 이유: ADR-052 결정 1, ADR-003.
- poppler(`pdftoppm`)·`pdf2image`를 쓰지 마라. 이유: 시스템 바이너리를 하나 더 들인다 — PDF 래스터화는 pypdfium2.
- 테스트에 `rapidfuzz` 등 새 테스트 의존성을 넣지 마라. 이유: 표준 라이브러리 `difflib`로 충분하다.
- OCR 테스트를 tesseract 부재 시 skip하게 만들지 마라. 이유: CI가 설치하며(step 0), skip은 검증 공백이다.
- 픽스처 파일을 수정·재생성하지 마라. 이유: 정답 텍스트와 짝이다(`SOURCE.md`).
- 기존 테스트를 깨뜨리지 마라
