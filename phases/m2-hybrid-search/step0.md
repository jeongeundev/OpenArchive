# Step 0: 파일 파싱

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"API 설계" 절의 `POST /api/documents` 행**, **"빈 파싱 결과 처리" 절**, "디렉토리 구조" 절의 `app/services/` 위치
- `/docs/PRD.md` — 핵심 기능 1(파일에서 텍스트를 추출해 저장, **원본 파일은 보관하지 않는다**), MVP 제외 사항(**HWP/HWPX 파싱 제외**, 원본 파일 보관·다운로드 제외)
- `/docs/ADR.md` — **ADR-017**(편집·버전 관리의 대상은 추출 텍스트이며 원본 파일이 아니다)
- `/CLAUDE.md` — "백엔드 비즈니스 로직은 `backend/app/services/`에 두고, API 라우터와 MCP 서버는 이를 재사용만 한다"
- **이전 phase 산출물**: `/backend/app/services/chunking.py` — 같은 디렉토리의 순수 함수다. 주석 밀도·타입 힌트·docstring 스타일을 여기에 맞춰라
- `/backend/migrations/002_tables.sql` — `documents.content_type`(pdf|docx|txt|md)과 `documents_content_not_blank` CHECK 제약

이전 phase에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

이 step은 **DB도 네트워크도 필요 없는 순수 함수 모듈 하나**를 만든다. 업로드 API(step 2)가 multipart로 받은 파일 바이트를 텍스트로 바꿀 때 쓴다.

순수 함수로 분리하는 이유는 API의 나머지 부분(권한·트랜잭션·트리거 상호작용)과 독립적으로 검증하기 위해서다. 파싱 규칙은 DB 없이 밀리초 단위로 확인할 수 있다.

**저장되는 것은 추출 텍스트뿐이다.** 원본 파일은 보관하지 않는다 (ADR-017). 이 모듈은 바이트를 받아 텍스트를 반환하고, 바이트를 어디에도 남기지 않는다.

## 작업

### 1. `backend/app/services/parsing.py`

```python
SUPPORTED_CONTENT_TYPES: tuple[str, ...] = ("pdf", "docx", "txt", "md")


class UnsupportedFileType(ValueError):
    """지원하지 않는 확장자. 사용자에게 보일 메시지를 담는다."""


class TextDecodeError(ValueError):
    """txt/md를 UTF-8로 읽지 못했다."""


def detect_content_type(filename: str) -> str:
    """파일명 확장자로 문서 유형을 판별한다. 4종이 아니면 UnsupportedFileType."""


def extract_text(data: bytes, content_type: str) -> str:
    """파일 바이트에서 텍스트를 추출한다."""
```

**반드시 만족해야 하는 불변식** — 구현 방식은 재량이지만 아래는 전부 지켜야 한다:

1. **지원 유형은 정확히 4종이다** — `pdf`(pypdf) · `docx`(python-docx) · `txt`/`md`(UTF-8 디코드). HWP·HWPX·XLSX·PPTX·이미지를 추가하지 마라. PRD의 MVP 제외 항목이다.
2. **추출 결과가 비어도 예외를 던지지 않는다.** 빈 문자열을 그대로 반환한다. 400 판정은 API 레이어(step 2)의 책임이다. 이 함수는 "추출"만 하고 "정책"을 갖지 않는다.
3. **`txt`/`md`는 UTF-8만 지원한다.** 디코드 실패는 `TextDecodeError`다. CP949·EUC-KR 자동 폴백을 넣지 마라 — 요청받지 않은 유연성이고, 잘못 추측한 인코딩은 에러 없이 깨진 텍스트를 저장해 임베딩까지 오염시킨다.
4. **결정론적이다.** 같은 바이트에 같은 텍스트. 시간·난수·파일시스템 상태에 의존하지 마라.
5. **파일시스템에 쓰지 마라.** pypdf와 python-docx 모두 file-like 객체를 받으므로 `io.BytesIO`로 메모리에서 처리한다. 임시 파일을 만들면 동시 요청에서 경쟁이 생기고 정리 책임이 따라온다.
6. **원본 바이트를 반환하거나 보관하지 마라.** 반환 타입은 `str` 하나다.
7. **새 런타임 의존성을 추가하지 마라.** `pypdf`와 `python-docx`는 `backend/pyproject.toml`에 이미 있다. `pdfplumber`·`PyMuPDF`·`textract`·`reportlab`을 추가하지 마라.
8. **여러 페이지·문단을 이어붙일 때 개행으로 구분한다.** 청킹(`chunk_text`)이 빈 줄을 문단 경계로 쓰므로, 페이지·문단 사이에 빈 줄이 남는 형태가 후속 단계와 맞는다.

`detect_content_type`은 확장자만 본다. 브라우저가 보내는 MIME 타입은 신뢰하지 않는다 — 클라이언트가 정하는 값이고 `.md`는 대개 `text/markdown`으로 오지도 않는다.

### 2. `backend/tests/test_parsing.py` — 먼저 작성한다

**구현보다 테스트를 먼저 작성하고 실패를 확인한 뒤 구현하라.** DB가 필요 없으므로 빠르게 돌아간다.

테스트 입력 파일은 **테스트 안에서 만든다.** 바이너리 픽스처를 저장소에 커밋하지 마라.

**DOCX 생성** — `python-docx`로 직접 만든다. 이 방법은 실측으로 확인됐다:

```python
import io
from docx import Document

buf = io.BytesIO()
doc = Document()
doc.add_paragraph("임베딩 잡은 트리거가 만든다")
doc.add_paragraph("두 번째 문단")
doc.save(buf)
docx_bytes = buf.getvalue()      # 약 36KB
```

**PDF 생성** — 최소 PDF를 바이트로 조립하는 헬퍼를 테스트 파일 안에 둔다. `reportlab` 같은 패키지를 추가하지 마라. 아래 형태가 pypdf로 정확히 추출되는 것을 **실측으로 확인했다**(567바이트, 추출 결과 `'embedding job trigger worker'`):

```python
def minimal_pdf(text: str) -> bytes:
    """xref 테이블 오프셋을 계산해 넣은 최소 PDF. text는 ASCII만 가능하다."""
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>"
        b"/MediaBox[0 0 612 792]/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length %d>>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj " % i + obj + b" endobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer <</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, xref_at
    )
    return bytes(out)
```

> **`xref` 테이블을 빼면 pypdf가 `PdfReadError: startxref not found`로 거부한다** — 실측 확인. 오프셋 계산을 생략한 더 짧은 버전을 시도하지 마라.

최소 아래를 확인한다.

1. **확장자 판별** — `report.pdf`→`pdf`, `manual.DOCX`(대문자)→`docx`, `notes.txt`→`txt`, `README.md`→`md`. 확장자가 없거나 `plan.hwp`·`data.xlsx`는 `UnsupportedFileType`.
2. **TXT/MD 추출** — 한국어 본문이 그대로 나온다. 문단 사이 빈 줄이 보존된다(청킹이 문단 경계로 쓴다).
3. **UTF-8이 아닌 txt** — `"한국어".encode("cp949")`를 넣으면 `TextDecodeError`. **조용히 깨진 문자열을 반환하지 않는 것**을 확인하라.
4. **DOCX 추출** — 위 방법으로 만든 DOCX에서 두 문단이 모두 나오고, 문단 순서가 보존된다.
5. **PDF 추출** — `minimal_pdf("embedding job trigger")`에서 그 문자열이 나온다.
6. **텍스트가 없는 PDF** — `minimal_pdf("")`는 **예외가 아니라 빈 문자열**을 반환한다(실측 확인). 스캔 이미지 PDF의 대역이며, step 2의 400 경로가 이 반환값에 기댄다.
7. **빈 바이트·손상된 파일** — 어떻게 다룰지 정하고 테스트로 고정하라. 파싱 실패가 500으로 새어나가면 안 된다.
8. **결정론** — 같은 바이트로 두 번 호출하면 결과가 같다.

> **한국어 PDF의 추출 품질은 이 step의 검증 대상이 아니다.** macOS `cupsfilter`로 만든 한국어 PDF에서 추출 결과가 `'٬߬ ੟੷ Ѣо...'`처럼 깨지는 것을 실측했다. 폰트가 서브셋 임베딩되면서 ToUnicode CMap이 빠진 경우이며, **pypdf의 결함도 우리가 고칠 수 있는 것도 아니다**(PDF 생성기에 달려 있다). 한국어 검증은 DOCX/TXT/MD로 하고, PDF 테스트는 ASCII로 한다. 실제 한국어 PDF 동작 확인은 M5 데모 준비 때 실제 샘플로 할 항목이다.

## Acceptance Criteria

```bash
cd backend
.venv/bin/ruff check .
.venv/bin/pytest tests/test_parsing.py -v
.venv/bin/pytest                  # 전체 통과
cd ..
bash scripts/check.sh             # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `app/services/parsing.py`에 있는가? (`ARCHITECTURE.md` 디렉토리 구조)
   - 순수 함수인가 — DB·네트워크·파일시스템·시간에 의존하지 않는가?
   - `backend/pyproject.toml`의 의존성 목록이 그대로인가?
   - 저장소에 바이너리 픽스처 파일이 추가되지 않았는가?
3. 결과에 따라 `phases/m2-hybrid-search/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **`detect_content_type`·`extract_text`의 정확한 시그니처, 예외 클래스 이름, 빈 결과가 예외가 아니라 빈 문자열이라는 점을 반드시 포함시켜라.** step 2가 이 함수들을 호출하고 400 판정을 붙인다.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **빈 추출 결과를 예외로 던지지 마라.** 이유: 400 응답 문구와 판정은 API 레이어의 책임이다. 여기서 던지면 step 2가 예외를 잡아 다시 400으로 바꾸는 왕복이 생긴다.
- **CP949·EUC-KR 자동 폴백을 넣지 마라.** 이유: 잘못 추측한 인코딩은 에러 없이 깨진 텍스트를 저장하고, 그 텍스트가 임베딩되어 검색까지 오염시킨다. 명시적 실패가 낫다.
- **원본 파일을 디스크나 DB에 저장하지 마라.** 이유: ADR-017 — 이 프로젝트는 원본 파일을 보관하지 않는다.
- **새 파싱 라이브러리를 추가하지 마라.** 이유: `pypdf`·`python-docx`로 충분하고, `pyproject.toml`의 의존성 목록은 심사에서 읽힌다.
- **바이너리 테스트 픽스처를 커밋하지 마라.** 이유: 위 두 생성 방법이 실측으로 확인됐고, 저장소에 정체를 알 수 없는 바이너리를 남기지 않는 편이 낫다.
- **OCR을 시도하지 마라.** 이유: 스캔 이미지 PDF는 400으로 거절하는 것이 설계 결정이다 (`ARCHITECTURE.md` "빈 파싱 결과 처리").
- **`app/services/`에 다른 파일(`search.py` 등)을 만들지 마라.** 이유: step 1의 범위다.
- **`app/api/` 아래 파일을 만들지 마라.** 이유: step 2의 범위다.
- 기존 테스트를 깨뜨리지 마라.
