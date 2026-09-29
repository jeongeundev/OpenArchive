import io
import re
import unicodedata
import zipfile
import zlib
from difflib import SequenceMatcher
from pathlib import Path

import olefile
import pytest
from docx import Document
from openpyxl import Workbook
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from app.services.parsing import (
    TextDecodeError,
    UnsupportedFileType,
    detect_content_type,
    extract_text,
    media_type_for,
    needs_ocr,
    ocr_text,
)


def minimal_pdf(text: str) -> bytes:
    """xref 테이블 오프셋을 계산해 넣은 최소 PDF. text는 ASCII만 가능하다."""
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>"
            b"/MediaBox[0 0 612 792]/Contents 5 0 R>>"
        ),
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
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer <</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref_at,
    )
    return bytes(out)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.pdf", "pdf"),
        ("manual.DOCX", "docx"),
        ("notes.txt", "txt"),
        ("README.md", "md"),
        ("공문.HWP", "hwp"),
        ("보고.hwpx", "hwpx"),
        ("예산.XLSX", "xlsx"),
        ("발표.pptx", "pptx"),
    ],
)
def test_detect_content_type(filename: str, expected: str) -> None:
    assert detect_content_type(filename) == expected


@pytest.mark.parametrize("filename", ["README", "plan.rtf", "data.csv"])
def test_detect_content_type_rejects_unsupported_files(filename: str) -> None:
    with pytest.raises(UnsupportedFileType):
        detect_content_type(filename)


@pytest.mark.parametrize("content_type", ["txt", "md"])
def test_extract_text_decodes_utf8_and_preserves_paragraph_breaks(content_type: str) -> None:
    text = "첫 번째 문단\n\n두 번째 문단"

    assert extract_text(text.encode(), content_type) == text


def test_extract_text_rejects_non_utf8_plain_text() -> None:
    with pytest.raises(TextDecodeError):
        extract_text("한국어".encode("cp949"), "txt")


def test_extract_text_reads_docx_paragraphs_in_order() -> None:
    buf = io.BytesIO()
    document = Document()
    document.add_paragraph("임베딩 잡은 트리거가 만든다")
    document.add_paragraph("두 번째 문단")
    document.save(buf)

    assert extract_text(buf.getvalue(), "docx") == (
        "임베딩 잡은 트리거가 만든다\n\n두 번째 문단"
    )


def test_extract_text_reads_pdf() -> None:
    assert extract_text(minimal_pdf("embedding job trigger"), "pdf") == "embedding job trigger"


def test_extract_text_returns_empty_string_for_pdf_without_text() -> None:
    assert extract_text(minimal_pdf(""), "pdf") == ""


@pytest.mark.parametrize("content_type", ["pdf", "docx", "hwp", "hwpx", "xlsx", "pptx"])
@pytest.mark.parametrize("data", [b"", b"not a document"])
def test_extract_text_normalizes_invalid_binary_document_errors(
    data: bytes, content_type: str
) -> None:
    with pytest.raises(ValueError, match="파일을 읽을 수 없습니다"):
        extract_text(data, content_type)


def test_extract_text_rejects_unsupported_content_type() -> None:
    with pytest.raises(UnsupportedFileType):
        extract_text(b"data", "rtf")


def test_media_type_is_fixed_by_extension_with_octet_stream_fallback():
    from app.services.parsing import media_type_for

    assert media_type_for("a.PDF") == "application/pdf"
    assert media_type_for("a.md") == "text/markdown; charset=utf-8"
    assert media_type_for("a.hwp") == "application/x-hwp"
    assert media_type_for("a.HWPX") == "application/hwp+zip"
    assert media_type_for("a.xlsx") == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert media_type_for("a.pptx") == (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert media_type_for("a.html") == "application/octet-stream"
    assert media_type_for("noext") == "application/octet-stream"


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    """출처와 이용 조건은 `fixtures/SOURCE.md`."""
    return (FIXTURES / name).read_bytes()


@pytest.mark.parametrize("content_type", ["hwp", "hwpx"])
def test_extract_text_reads_hangul_paragraphs_and_table_cells_in_order(
    content_type: str,
) -> None:
    text = extract_text(fixture(f"committee_result.{content_type}"), content_type)
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n")]

    # 문단마다 빈 줄로 나뉘어야 청킹(#103)이 문단 경계를 쓴다.
    assert "2026년 제38차 위원회 결과" in paragraphs
    assert "가. 유진이엔티(주)에 대한 청문에 관한 건" in paragraphs
    # 표 셀 안의 문단도 추출한다 — 보도자료는 머리말·담당자를 표에 둔다.
    assert "가. 방송지원정책과" in paragraphs
    # 본문 순서를 지킨다.
    title = paragraphs.index("2026년 제38차 위원회 결과")
    agenda = paragraphs.index("가. 유진이엔티(주)에 대한 청문에 관한 건")
    decision = next(i for i, p in enumerate(paragraphs) if p.startswith("o 방미통위는"))
    assert title < agenda < decision
    # 빈 문단은 남기지 않는다.
    assert "" not in paragraphs


@pytest.mark.parametrize("name", ["committee_result", "tax_administration"])
def test_hwp_and_hwpx_of_the_same_document_extract_the_same_text(name: str) -> None:
    hwp = extract_text(fixture(f"{name}.hwp"), "hwp")

    assert hwp == extract_text(fixture(f"{name}.hwpx"), "hwpx")
    # HWP 문단 텍스트의 제어 문자(표·그림 자리 등)가 본문에 새지 않는다.
    assert not re.search(r"[\x00-\x08\x0b-\x1f]", hwp)


@pytest.mark.parametrize(
    ("flag", "message"),
    [(0x02, "암호가 걸린 HWP 문서"), (0x04, "배포용 HWP 문서")],
)
def test_extract_text_rejects_encrypted_hwp(tmp_path: Path, flag: int, message: str) -> None:
    path = tmp_path / "encrypted.hwp"
    path.write_bytes(fixture("committee_result.hwp"))
    with olefile.OleFileIO(str(path), write_mode=True) as ole:
        header = bytearray(ole.openstream("FileHeader").read())
        header[36] |= flag  # 속성 플래그: bit 1 암호, bit 2 배포용
        ole.write_stream("FileHeader", bytes(header))

    with pytest.raises(ValueError, match=message):
        extract_text(path.read_bytes(), "hwp")


def test_extract_text_rejects_zip_that_is_not_hwpx() -> None:
    buf = io.BytesIO()
    Document().save(buf)  # 섹션 XML이 없는 다른 ZIP 문서

    with pytest.raises(ValueError, match="HWPX 파일을 읽을 수 없습니다"):
        extract_text(buf.getvalue(), "hwpx")


def hwpx_with_sections(sections: list[str]) -> bytes:
    """픽스처 HWPX의 section0.xml을 복제·변형한 섹션들로 바꾼 사본. 픽스처 파일은 그대로 둔다."""
    source = zipfile.ZipFile(io.BytesIO(fixture("committee_result.hwpx")))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as target:
        for item in source.infolist():
            if item.filename != "Contents/section0.xml":
                target.writestr(item, source.read(item))
        for number, xml in enumerate(sections):
            target.writestr(f"Contents/section{number}.xml", xml)
    return buf.getvalue()


def test_extract_text_reads_hwpx_sections_in_numeric_order() -> None:
    original = fixture_section_xml()
    sections = [
        original.replace("2026년 제38차 위원회 결과", f"섹션 {number}") for number in range(11)
    ]

    text = extract_text(hwpx_with_sections(sections), "hwpx")

    # 이름순이면 section10이 section2 앞에 온다.
    positions = [text.index(f"섹션 {number}\n") for number in range(11)]
    assert positions == sorted(positions)


def test_extract_text_turns_hwpx_tab_into_tab_character() -> None:
    section = fixture_section_xml().replace(
        "<hp:t>가. 유진이엔티", "<hp:t>가.<hp:tab/>유진이엔티"
    )

    text = extract_text(hwpx_with_sections([section]), "hwpx")

    assert "가.\t유진이엔티(주)에 대한 청문에 관한 건" in text



def hwp_without_text(path: Path) -> bytes:
    """픽스처 HWP의 본문 섹션을 레코드 없는 빈 섹션으로 바꾼 사본을 `path`에 쓰고 돌려준다."""
    path.write_bytes(fixture("committee_result.hwp"))
    with olefile.OleFileIO(str(path), write_mode=True) as ole:
        compressor = zlib.compressobj(wbits=-15)
        empty = compressor.compress(b"") + compressor.flush()
        # write_stream은 크기를 바꾸지 못한다 — deflate 끝 뒤의 0 채움은 해제 때 버려진다.
        size = ole.get_size("BodyText/Section0")
        ole.write_stream("BodyText/Section0", empty.ljust(size, b"\0"))
    return path.read_bytes()


def hwpx_without_text() -> bytes:
    """픽스처 HWPX에서 글자 요소를 모두 비운 사본. 문단·표 구조는 남는다."""
    section = re.sub(r"<hp:t\b[^>/]*>.*?</hp:t>", "<hp:t/>", fixture_section_xml(), flags=re.DOTALL)
    return hwpx_with_sections([section])


def test_extract_text_returns_empty_string_for_hangul_document_without_text(
    tmp_path: Path,
) -> None:
    # 빈 결과는 여기서 오류로 만들지 않는다 — 문서 서비스의 EmptyExtractedText가 판정한다.
    assert extract_text(hwp_without_text(tmp_path / "empty.hwp"), "hwp") == ""
    assert extract_text(hwpx_without_text(), "hwpx") == ""

def fixture_section_xml() -> str:
    with zipfile.ZipFile(io.BytesIO(fixture("committee_result.hwpx"))) as archive:
        return archive.read("Contents/section0.xml").decode("utf-8")


def test_extract_text_reads_xlsx_sheets_with_cached_formula_values() -> None:
    # Numbers가 내보낸 파일이다. 수식 셀(합계)은 저장할 때 계산된 값이 캐시돼 있고,
    # Numbers가 덧붙인 「내보내기 요약」 시트도 파일 안의 시트이므로 함께 읽는다.
    text = extract_text(fixture("office_budget.xlsx"), "xlsx")

    blocks = text.split("\n\n")
    assert [block.splitlines()[0] for block in blocks] == ["내보내기 요약", "예산", "일정"]
    assert blocks[1] == "예산\n표 1\n항목\t금액\n인건비\t1200\n운영비\t300\n합계\t1500"
    assert "=SUM" not in text


def test_extract_text_keeps_xlsx_columns_and_skips_empty_rows_and_sheets() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "명단"
    sheet["B1"] = "이름"
    sheet["D1"] = "부서"
    sheet["B3"] = "김하나"
    workbook.create_sheet("빈 시트")
    buf = io.BytesIO()
    workbook.save(buf)

    # 앞·가운데 빈 칸은 탭으로 남겨 열 위치를 지키고, 줄 끝 빈 칸과 빈 행은 버린다.
    # 값이 하나도 없는 시트는 시트명도 내지 않는다.
    assert extract_text(buf.getvalue(), "xlsx") == "명단\n\t이름\t\t부서\n\t김하나"


def test_extract_text_leaves_uncached_xlsx_formula_cells_empty() -> None:
    # openpyxl처럼 계산 엔진 없이 쓴 파일에는 수식의 값이 캐시되지 않는다. 값을 계산하지도,
    # 수식 문자열을 본문에 넣지도 않고 빈 칸으로 둔다.
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "예산"
    sheet.append(["인건비", 1200])
    sheet.append(["운영비", 300])
    sheet.append(["합계", "=SUM(B1:B2)"])
    buf = io.BytesIO()
    workbook.save(buf)

    assert extract_text(buf.getvalue(), "xlsx") == "예산\n인건비\t1200\n운영비\t300\n합계"


def test_extract_text_reads_pptx_slides_with_presenter_notes() -> None:
    # Keynote가 내보낸 파일이다. 빈 자리표시자는 건너뛰고, 텍스트 상자 안 줄바꿈은
    # 줄바꿈으로, 슬라이드 사이는 빈 줄로 남는다.
    text = extract_text(fixture("office_briefing.pptx"), "pptx")

    assert text == (
        "2차 평가 준비\nHA 실증\n첫 슬라이드 발표자 노트"
        "\n\n"
        "형식 확장\n한글 문서\n오피스 문서\n둘째 슬라이드 노트"
    )


def test_extract_text_reads_pptx_group_and_table_shapes() -> None:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    group.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1)).text = "묶인 상자"
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "단계"
    table.cell(0, 1).text = "기한"
    table.cell(1, 0).text = "착수"
    buf = io.BytesIO()
    presentation.save(buf)

    # 표는 xlsx와 같이 행마다 한 줄, 셀은 탭으로 구분한다.
    assert extract_text(buf.getvalue(), "pptx") == "묶인 상자\n단계\t기한\n착수"


def test_extract_text_skips_empty_pptx_slides_between_slides() -> None:
    presentation = Presentation()
    for text in ["첫 슬라이드", "", "셋째 슬라이드"]:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        if text:
            slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1)).text = text
    buf = io.BytesIO()
    presentation.save(buf)

    # 빈 슬라이드는 빈 줄을 겹쳐 남기지 않는다 — 슬라이드 경계는 빈 줄 하나다.
    assert extract_text(buf.getvalue(), "pptx") == "첫 슬라이드\n\n셋째 슬라이드"


def test_extract_text_returns_empty_string_for_office_documents_without_text() -> None:
    workbook = Workbook()
    xlsx = io.BytesIO()
    workbook.save(xlsx)
    presentation = Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[0])
    pptx = io.BytesIO()
    presentation.save(pptx)

    # 빈 결과는 여기서 오류로 만들지 않는다 — 문서 서비스의 EmptyExtractedText가 판정한다.
    assert extract_text(xlsx.getvalue(), "xlsx") == ""
    assert extract_text(pptx.getvalue(), "pptx") == ""


def normalize_ocr(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def assert_ocr_matches(text: str, reference: str) -> str:
    normalized = normalize_ocr(text)
    expected = normalize_ocr(fixture(reference).decode())
    assert SequenceMatcher(None, expected, normalized, autojunk=False).ratio() >= 0.85
    assert "국세행정개혁위원회" in normalized
    return normalized


def test_ocr_reads_a_scanned_image() -> None:
    assert_ocr_matches(ocr_text(fixture("scan_tax_page1.jpg"), "jpg"), "scan_tax_page1.txt")


def test_ocr_reads_every_page_of_a_scanned_pdf() -> None:
    text = ocr_text(fixture("scan_tax_pages.pdf"), "pdf")
    normalized = assert_ocr_matches(text, "scan_tax_pages.txt")
    assert normalized.index("국세행정개혁위원회") < normalized.index("소상공인")
    assert "\n\n" in text


def test_extract_text_does_not_ocr(monkeypatch) -> None:
    def unexpected_ocr(*args, **kwargs):
        pytest.fail("extract_text must not call OCR")

    monkeypatch.setattr("app.services.parsing.ocr_text", unexpected_ocr)
    assert extract_text(fixture("scan_tax_page1.jpg"), "jpg") == ""
    assert extract_text(fixture("scan_tax_pages.pdf"), "pdf").strip() == ""


@pytest.mark.parametrize("content_type", ["png", "jpg", "jpeg", "pdf",
                                         "docx", "hwp", "hwpx", "txt", "md", "xlsx", "pptx"])
@pytest.mark.parametrize("text", ["", " \t\n", "텍스트"])
def test_needs_ocr(content_type: str, text: str) -> None:
    expected = content_type in ("png", "jpg", "jpeg") or (
        content_type == "pdf" and not text.strip()
    )
    assert needs_ocr(content_type, text) is expected


@pytest.mark.parametrize("content_type", ["png", "jpg", "jpeg", "pdf"])
def test_ocr_text_rejects_corrupt_image(content_type: str) -> None:
    with pytest.raises(ValueError, match=f"{content_type.upper()} 파일을 읽을 수 없습니다"):
        ocr_text(b"not an image", content_type)


def test_ocr_respects_exif_orientation() -> None:
    buf = io.BytesIO()
    with (
        Image.open(io.BytesIO(fixture("scan_tax_page1.jpg"))) as source,
        source.transpose(Image.Transpose.ROTATE_90) as rotated,
    ):
        exif = rotated.getexif()
        exif[274] = 6  # 저장된 반시계 회전을 시계 방향으로 바로 세운다.
        rotated.save(buf, format="JPEG", exif=exif)
    assert_ocr_matches(ocr_text(buf.getvalue(), "jpg"), "scan_tax_page1.txt")


@pytest.mark.parametrize(
    ("filename", "content_type", "media_type"),
    [("scan.JPG", "jpg", "image/jpeg"), ("scan.jpeg", "jpeg", "image/jpeg"),
     ("scan.png", "png", "image/png")],
)
def test_image_content_types(filename: str, content_type: str, media_type: str) -> None:
    assert detect_content_type(filename) == content_type
    assert media_type_for(filename) == media_type


@pytest.mark.parametrize(
    "message",
    ["Error opening data file /missing/kor.traineddata Failed loading language 'kor'",
     "Error opening data file /missing/eng.traineddata Failed loading language 'eng'"],
)
def test_ocr_preserves_language_loading_errors(monkeypatch, message: str) -> None:
    import pytesseract

    error = pytesseract.TesseractError(1, message)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(pytesseract, "image_to_string", fail)
    with pytest.raises(pytesseract.TesseractError) as caught:
        ocr_text(fixture("scan_tax_page1.jpg"), "jpg")
    assert caught.value is error


def test_ocr_preserves_missing_engine_error(monkeypatch) -> None:
    import pytesseract

    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "/missing/tesseract")
    with pytest.raises(pytesseract.TesseractNotFoundError):
        ocr_text(fixture("scan_tax_page1.jpg"), "jpg")


def test_ocr_normalizes_engine_execution_error(monkeypatch) -> None:
    import pytesseract

    error = pytesseract.TesseractError(1, "Image processing failed")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(pytesseract, "image_to_string", fail)
    with pytest.raises(ValueError, match="JPG 파일을 읽을 수 없습니다") as caught:
        ocr_text(fixture("scan_tax_page1.jpg"), "jpg")
    assert caught.value.__cause__ is error
