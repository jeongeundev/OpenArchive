import io
import re
import zipfile
from pathlib import Path

import olefile
import pytest
from docx import Document

from app.services.parsing import (
    TextDecodeError,
    UnsupportedFileType,
    detect_content_type,
    extract_text,
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
    ],
)
def test_detect_content_type(filename: str, expected: str) -> None:
    assert detect_content_type(filename) == expected


@pytest.mark.parametrize("filename", ["README", "plan.rtf", "data.xlsx"])
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


@pytest.mark.parametrize("content_type", ["pdf", "docx", "hwp", "hwpx"])
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
    assert media_type_for("a.html") == "application/octet-stream"
    assert media_type_for("noext") == "application/octet-stream"


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    """공공누리 제1유형 공개 보도자료. 출처는 `fixtures/SOURCE.md`."""
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


def fixture_section_xml() -> str:
    with zipfile.ZipFile(io.BytesIO(fixture("committee_result.hwpx"))) as archive:
        return archive.read("Contents/section0.xml").decode("utf-8")
