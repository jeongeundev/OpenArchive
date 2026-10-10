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
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

from openarchive.services.parsing import (
    TextDecodeError,
    UnsupportedFileType,
    detect_content_type,
    extract_text,
    media_type_for,
    needs_ocr,
    ocr_text,
)


def minimal_pdf(text: str, font: bytes = b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
                *extra_objs: bytes) -> bytes:
    """xref 테이블 오프셋을 계산해 넣은 최소 PDF. text는 ASCII만 가능하다.

    `font`는 4번 객체, `extra_objs`는 6번부터 붙는다(글꼴이 참조하는 객체용).
    """
    return pdf_from_stream(f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode(), font, *extra_objs)


def pdf_from_stream(stream: bytes, font: bytes = b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
                    *extra_objs: bytes) -> bytes:
    """내용 스트림을 그대로 담은 한 쪽짜리 PDF. 글꼴은 `/F1`이다."""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>"
            b"/MediaBox[0 0 612 792]/Contents 5 0 R>>"
        ),
        font,
        b"<</Length %d>>\nstream\n%s\nendstream" % (len(stream), stream),
        *extra_objs,
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


def hwp_style_pdf(*runs: tuple[int, int, str]) -> bytes:
    """한글 오피스 출력 모양 — 글자 덩어리마다 `q · cm(축소·상하 반전) · BT · Tm · TJ · ET · Q`로 따로 그린다.

    `runs`는 (x, y, 텍스트)이고 좌표는 축소 전 단위(1/0.12pt), y는 아래로 커진다.
    """
    return pdf_from_stream(b"".join(
        b"q 0.12 0 0 -0.12 0 841 cm BT /F1 100 Tf 1 0 0 -1 %d %d Tm [(%s)] TJ ET Q\n"
        % (x, y, text.encode())
        for x, y, text in runs
    ))


def test_extract_text_separates_pdf_table_cells_drawn_apart_on_one_line() -> None:
    """표 셀 사이에 공백 글자가 없어도 같은 줄에서 떨어져 그려졌으면 공백으로 가른다(#176)."""
    data = hwp_style_pdf((1000, 1800, "16,815"), (1500, 1800, "5,209"), (2000, 1800, "11,320"))

    assert extract_text(data, "pdf") == "16,815 5,209 11,320"


def test_extract_text_joins_a_word_wrapped_at_the_right_margin() -> None:
    """오른쪽 여백까지 찬 줄이 낱말 중간에서 바뀌면 잇는다 — 한국어 규정 PDF에 흔하다(#176).

    판면은 쪽 너비에서 왼쪽 여백을 양쪽으로 뺀 폭이다. x=2000(240pt)에서 시작한 첫 줄이 612pt 쪽의 오른쪽
    여백(372pt)을 넘는다.
    """
    data = hwp_style_pdf((2000, 1800, "The archive keeps every infor"), (2000, 1950, "mation safe"))

    assert extract_text(data, "pdf") == "The archive keeps every information safe"


def test_extract_text_keeps_the_line_break_after_a_line_short_of_the_margin() -> None:
    """여백에 못 미친 줄 뒤는 낱말이 끊긴 자리가 아니다 — 제목·목록·쪽 번호 뒤 줄바꿈을 지킨다(#176)."""
    data = hwp_style_pdf((2000, 1800, "Article"), (2000, 1950, "The archive keeps every record"))

    assert extract_text(data, "pdf") == "Article\nThe archive keeps every record"


def test_extract_text_keeps_the_line_break_after_a_full_line_ending_with_a_space() -> None:
    """꽉 찬 줄이라도 공백으로 끝났으면 낱말 경계다 — 잇지 않는다(#176)."""
    data = hwp_style_pdf((2000, 1800, "The archive keeps every "), (2000, 1950, "record safe"))

    assert extract_text(data, "pdf").split() == ["The", "archive", "keeps", "every", "record", "safe"]


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        (((1000, 1800, "Chapter One Overview"), (1000, 1950, "Scope")), "Chapter One Overview\nScope"),
        (
            ((1000, 1800, "Title"), (1000, 1950, "- item one"), (1000, 2100, "- item two longer"),
             (1000, 2250, "- item three")),
            "Title\n- item one\n- item two longer\n- item three",
        ),
    ],
)
def test_extract_text_keeps_line_breaks_on_a_page_without_a_full_line(runs, expected) -> None:
    """판면을 채운 줄이 없는 쪽(표지·목차·목록)에서는 가장 긴 줄도 꽉 찬 줄이 아니다(#176)."""
    assert extract_text(hwp_style_pdf(*runs), "pdf") == expected


def test_extract_text_keeps_a_separately_drawn_space_at_the_end_of_a_full_line() -> None:
    """줄 끝 공백을 따로 그린 꽉 찬 줄도 낱말 경계다 — pdfium은 그 공백을 버리지만 원본에 있다(#176)."""
    data = hwp_style_pdf(
        (2000, 1800, "The archive keeps every"), (3370, 1800, " "), (2000, 1950, "record safe")
    )

    assert extract_text(data, "pdf").split() == ["The", "archive", "keeps", "every", "record", "safe"]


def test_ocr_keeps_the_same_layer_text_as_extract_text_for_text_pages() -> None:
    """OCR 경로의 텍스트 쪽도 업로드 추출과 같은 셀 구분을 쓴다(#176)."""
    data = hwp_style_pdf((1000, 1800, "16,815"), (1500, 1800, "5,209"))
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.add_blank_page(612, 792)  # 텍스트 레이어가 빈 쪽 — OCR 대상
    buffer = io.BytesIO()
    writer.write(buffer)

    assert ocr_text(buffer.getvalue(), "pdf").split("\n\n")[0] == "16,815 5,209"


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
    from openarchive.services.parsing import media_type_for

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
    """픽스처 HWPX에서 글자 요소를 모두 비우고 그림(BinData)을 뺀 사본. 문단·표 구조는 남는다.

    그림이 남으면 그림뿐인 문서로 OCR 대상이 된다(#177).
    """
    section = re.sub(r"<hp:t\b[^>/]*>.*?</hp:t>", "<hp:t/>", fixture_section_xml(), flags=re.DOTALL)
    source = zipfile.ZipFile(io.BytesIO(hwpx_with_sections([section])))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as target:
        for item in source.infolist():
            if not item.filename.startswith("BinData/"):
                target.writestr(item, source.read(item))
    return buf.getvalue()


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

    monkeypatch.setattr("openarchive.services.parsing.ocr_text", unexpected_ocr)
    assert extract_text(fixture("scan_tax_page1.jpg"), "jpg") == ""
    assert extract_text(fixture("scan_tax_pages.pdf"), "pdf").strip() == ""


@pytest.mark.parametrize("content_type", ["png", "jpg", "jpeg", "hwp", "txt", "md"])
def test_needs_ocr_for_images_only_among_non_pdf_non_office_types(content_type: str) -> None:
    assert needs_ocr(content_type, b"") is (content_type in ("png", "jpg", "jpeg"))


def pptx_bytes(slides: list[list[str]]) -> bytes:
    """슬라이드마다 항목 목록 — `"그림"`은 스캔 이미지 그림, 그 밖의 문자열은 글상자다."""
    presentation = Presentation()
    for items in slides:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        for item in items:
            if item == "그림":
                slide.shapes.add_picture(
                    io.BytesIO(fixture("scan_tax_page1.jpg")), Inches(0.5), Inches(0.2), height=Inches(7)
                )
            else:
                slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1)).text = item
    buf = io.BytesIO()
    presentation.save(buf)
    return buf.getvalue()


def pptx_with_linked_picture(slides: list[list[str]]) -> bytes:
    """마지막 슬라이드에 내장하지 않고 외부 링크만 건 그림을 더한 사본."""
    from pptx.opc.constants import RELATIONSHIP_TYPE
    from pptx.oxml.ns import qn

    presentation = Presentation(io.BytesIO(pptx_bytes([*slides, ["그림"]])))
    slide = presentation.slides[-1]
    blip = slide.shapes[0]._pic.blipFill.blip
    del blip.attrib[qn("r:embed")]
    blip.set(qn("r:link"), slide.part.relate_to(
        "https://example.com/scan.jpg", RELATIONSHIP_TYPE.IMAGE, is_external=True
    ))
    buf = io.BytesIO()
    presentation.save(buf)
    return buf.getvalue()


def docx_bytes(text: str = "", picture: bool = True) -> bytes:
    document = Document()
    if text:
        document.add_paragraph(text)
    if picture:
        document.add_picture(io.BytesIO(fixture("scan_tax_page1.jpg")), width=Inches(6))
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def xlsx_bytes(text: str = "", picture: bool = True) -> bytes:
    from openpyxl.drawing.image import Image as SheetImage

    workbook = Workbook()
    if text:
        workbook.active["A1"] = text
    if picture:
        workbook.active.add_image(SheetImage(io.BytesIO(fixture("scan_tax_page1.jpg"))), "B2")
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def hwpx_with_only_picture() -> bytes:
    """글자도 그림도 없는 HWPX에 스캔 이미지 한 장을 그림(BinData)으로 넣은 사본."""
    buf = io.BytesIO(hwpx_without_text())
    with zipfile.ZipFile(buf, "a") as archive:
        archive.writestr("BinData/image1.jpg", fixture("scan_tax_page1.jpg"))
    return buf.getvalue()


@pytest.mark.parametrize(
    ("content_type", "data", "expected"),
    [
        ("pptx", fixture("scan_tax_page1_slide.pptx"), True),
        # 쪽 단위 — 텍스트 슬라이드 사이에 그림뿐인 슬라이드가 끼어 있다(혼합 PDF와 같은 기준)
        ("pptx", pptx_bytes([["첫 슬라이드"], ["그림"], ["셋째 슬라이드"]]), True),
        # 텍스트가 있는 슬라이드의 그림은 인식하지 않는다
        ("pptx", pptx_bytes([["첫 슬라이드", "그림"]]), False),
        ("pptx", fixture("office_briefing.pptx"), False),
        # 외부 링크만 건 그림은 파일 안에 없다 — 읽을 그림이 없는 슬라이드로 본다
        ("pptx", pptx_with_linked_picture([["첫 슬라이드"]]), False),
        # 쪽이 없는 형식은 문서 전체의 텍스트가 빌 때만 그림을 인식한다
        ("docx", docx_bytes(), True),
        ("docx", docx_bytes("본문"), False),
        ("docx", docx_bytes(picture=False), False),
        ("xlsx", xlsx_bytes(), True),
        ("xlsx", xlsx_bytes("값"), False),
        ("xlsx", fixture("office_budget.xlsx"), False),
        ("hwpx", hwpx_with_only_picture(), True),
        # 본문이 있는 한글 문서의 기관 로고는 인식하지 않는다
        ("hwpx", fixture("committee_result.hwpx"), False),
        ("hwpx", hwpx_without_text(), False),
    ],
    ids=["pptx-picture-only", "pptx-picture-slide-between-text", "pptx-picture-on-text-slide",
         "pptx-text", "pptx-linked-picture", "docx-picture-only", "docx-text-and-picture", "docx-empty",
         "xlsx-picture-only", "xlsx-text-and-picture", "xlsx-text", "hwpx-picture-only",
         "hwpx-text-and-logos", "hwpx-empty"],
)
def test_needs_ocr_for_office_documents_whose_pictures_carry_the_only_text(
    content_type: str, data: bytes, expected: bool
) -> None:
    """그림뿐인 오피스 문서는 거부하지 않고 그림 속 글자를 인식한다(#177, ADR-052 결정 2)."""
    assert needs_ocr(content_type, data) is expected


@pytest.mark.parametrize(
    ("content_type", "data"),
    [
        ("pptx", fixture("scan_tax_page1_slide.pptx")),
        ("docx", docx_bytes()),
        ("xlsx", xlsx_bytes()),
        ("hwpx", hwpx_with_only_picture()),
        # 링크 그림만 있는 슬라이드가 함께 있어도 내장 그림은 인식한다
        ("pptx", pptx_with_linked_picture([["그림"]])),
    ],
    ids=["pptx", "docx", "xlsx", "hwpx", "pptx-with-linked-picture-slide"],
)
def test_ocr_reads_the_pictures_of_a_picture_only_office_document(
    content_type: str, data: bytes
) -> None:
    assert_ocr_matches(ocr_text(data, content_type), "scan_tax_page1.txt")


def test_ocr_keeps_text_slides_and_reads_only_the_picture_slide() -> None:
    text = ocr_text(pptx_bytes([["첫 슬라이드"], ["그림"], ["셋째 슬라이드"]]), "pptx")

    # 인식 결과의 앞뒤 공백·줄바꿈은 tesseract가 정한다 — 슬라이드 순서와 경계만 본다
    assert text.startswith("첫 슬라이드\n\n")
    assert text.endswith("\n\n셋째 슬라이드")
    assert "국세행정개혁위원회" in normalize_ocr(text.removeprefix("첫 슬라이드").removesuffix("셋째 슬라이드"))


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (minimal_pdf("embedding job trigger"), False),
        (minimal_pdf(""), True),
        (minimal_pdf("   "), True),
        (fixture("scan_tax_pages.pdf"), True),
        # 텍스트 레이어가 있는 쪽 사이에 스캔 쪽이 끼어 있다 — 문서 전체로 보면 텍스트가 있지만
        # 2쪽은 OCR 없이는 빈다
        (fixture("mixed_tax_pages.pdf"), True),
    ],
    ids=["text", "empty", "blank", "scan", "mixed"],
)
def test_needs_ocr_for_pdf_when_any_page_has_no_text_layer(data: bytes, expected: bool) -> None:
    assert needs_ocr("pdf", data) is expected


def type3_pdf(glyph: str) -> bytes:
    """`A`를 `glyph` 이름의 글리프로 그리는 Type3 글꼴 PDF. ToUnicode가 없다."""
    name = glyph.encode()
    font = (
        b"<</Type/Font/Subtype/Type3/FontBBox[0 0 1000 1000]/FontMatrix[0.001 0 0 0.001 0 0]"
        b"/CharProcs<</%s 6 0 R>>/Encoding<</Type/Encoding/Differences[65/%s]>>"
        b"/FirstChar 65/LastChar 65/Widths[500]/Resources<<>>>>" % (name, name)
    )
    return minimal_pdf("AAA", font, b"<</Length 8>>\nstream\n500 0 d0\nendstream")


def page_of(data: bytes, keep: int) -> bytes:
    """한 쪽만 남긴 PDF."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    for index in range(len(writer.pages) - 1, -1, -1):
        if index != keep:
            writer.remove_page(index)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def first_page_of(data: bytes, *, strip_to_unicode: bool = False) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    for index in range(len(writer.pages) - 1, 0, -1):
        writer.remove_page(index)
    if strip_to_unicode:
        for font in writer.pages[0]["/Resources"]["/Font"].values():
            del font.get_object()[NameObject("/ToUnicode")]
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # macOS cupsfilter가 만든 PDF — Type0·Identity-H 글꼴에 ToUnicode가 없어, 레이어 텍스트가
        # 글리프 번호를 글자로 읽은 '࠺ ࢑ ӏ…'다(빈 쪽이 아니다)
        (fixture("garbled_travel_rule.pdf"), True),
        # 한글 보도자료 1쪽(Type0·Identity-H) — ToUnicode를 지우기 전후
        (first_page_of(fixture("mixed_tax_pages.pdf")), False),
        (first_page_of(fixture("mixed_tax_pages.pdf"), strip_to_unicode=True), True),
        # Type3: 표준 글리프 이름은 글자로 읽히지만 HWP의 `/HFT1` 같은 자체 이름은 이름이 그대로 새어 나온다
        (type3_pdf("A"), False),
        (type3_pdf("g1"), True),
    ],
    ids=["no-to-unicode-fixture", "type0-with-to-unicode", "type0-without-to-unicode",
         "type3-standard-names", "type3-private-names"],
)
def test_needs_ocr_for_pdf_when_a_page_text_layer_is_garbled(data: bytes, expected: bool) -> None:
    """레이어 텍스트가 있어도 글자 정보(ToUnicode)가 없어 글자로 읽을 수 없는 쪽은 OCR한다(#191).

    판정은 텍스트 통계가 아니라 글꼴 구조로 한다. 실측: 정상 PDF 4,705쪽 중 걸린 쪽 0, 같은 PDF에서
    ToUnicode만 지운 4,704쪽은 전부 걸림 — 의심 문자 비율(5%)은 Type3 글리프 이름이 ASCII라 52쪽을 놓쳤다.
    """
    assert extract_text(data, "pdf").strip()  # 빈 쪽 판정과 무관하다
    assert needs_ocr("pdf", data) is expected


def test_ocr_reads_a_garbled_text_layer_page_as_korean() -> None:
    text = normalize_ocr(ocr_text(fixture("garbled_travel_rule.pdf"), "pdf"))

    assert "출장비정산규정" in text
    expected = normalize_ocr(fixture("garbled_travel_rule.txt").decode())
    assert SequenceMatcher(None, expected, text, autojunk=False).ratio() >= 0.85


def test_ocr_keeps_text_layer_pages_and_reads_only_the_scanned_page(monkeypatch) -> None:
    """혼합 PDF: 텍스트 쪽은 레이어 그대로, 스캔 쪽만 인식해 쪽 순서대로 잇는다(#167)."""
    import pytesseract

    calls = []
    real = pytesseract.image_to_string

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(pytesseract, "image_to_string", counting)
    data = fixture("mixed_tax_pages.pdf")
    text = ocr_text(data, "pdf")

    assert len(calls) == 1  # 텍스트 레이어가 있는 1·3쪽은 인식하지 않는다
    # 레이어 텍스트는 업로드 추출과 같은 것이다(#176 — pdfium 글자 위치로 만든 쪽 텍스트)
    pages = [extract_text(page_of(data, index), "pdf") for index in range(3)]
    assert text.count(pages[0]) == 1 and text.count(pages[2]) == 1  # 레이어 그대로, 한 번씩
    normalized = normalize_ocr(text)
    ocr_page = normalized.index("하반기세무조사운영방향")  # 2쪽에만 있는 안건 제목
    assert normalized.index(normalize_ocr(pages[0])) < ocr_page
    assert ocr_page < normalized.index(normalize_ocr(pages[2]))
    expected = normalize_ocr(fixture("mixed_tax_pages.txt").decode())
    assert SequenceMatcher(None, expected, normalized, autojunk=False).ratio() >= 0.85


@pytest.mark.parametrize("content_type", ["png", "jpg", "jpeg", "pdf"])
def test_ocr_text_rejects_corrupt_image(content_type: str) -> None:
    with pytest.raises(ValueError, match=f"{content_type.upper()} 파일을 읽을 수 없습니다"):
        ocr_text(b"not an image", content_type)


@pytest.mark.parametrize("content_type", ["png", "jpg", "jpeg"])
def test_extract_text_rejects_an_unreadable_image(content_type: str) -> None:
    """이미지는 요청 안에서 OCR하지 않지만 읽을 수 있는지는 확인한다 — 다른 형식처럼 업로드에서 거부한다.

    워커까지 가면 같은 파일을 몇 번 다시 읽어도 결과가 같은데 재시도 예산을 다 쓴 뒤에야 실패한다.
    """
    with pytest.raises(ValueError, match=f"{content_type.upper()} 파일을 읽을 수 없습니다"):
        extract_text(b"not an image", content_type)


def test_extract_text_rejects_a_truncated_image() -> None:
    """머리만 멀쩡하고 뒤가 잘린 파일은 열기만 해서는 통과한다 — 끝까지 디코드해 본다."""
    data = fixture("scan_tax_page1.jpg")

    with pytest.raises(ValueError, match="JPG 파일을 읽을 수 없습니다"):
        extract_text(data[: len(data) // 2], "jpg")


def test_extract_text_accepts_a_readable_image_without_ocr() -> None:
    assert extract_text(fixture("scan_tax_page1.jpg"), "jpg") == ""


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
