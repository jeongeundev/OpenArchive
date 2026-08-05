"""업로드 파일 바이트에서 추출 텍스트를 만드는 순수 함수.

DB·네트워크·파일시스템을 건드리지 않고 메모리 안에서만 처리한다. 반환된 추출
텍스트만 저장하며, 입력으로 받은 원본 파일 바이트는 보관하지 않는다 (ADR-017).

빈 추출 결과를 거부하는 정책은 API 레이어의 책임이다. 이 모듈은 스캔 이미지 PDF
처럼 텍스트가 없는 파일도 예외로 바꾸지 않고 빈 문자열을 그대로 반환한다.
"""

import io
from pathlib import PurePath

from docx import Document
from pypdf import PdfReader

SUPPORTED_CONTENT_TYPES: tuple[str, ...] = ("pdf", "docx", "txt", "md")


class UnsupportedFileType(ValueError):
    """지원하지 않는 확장자. 사용자에게 보일 메시지를 담는다."""


class TextDecodeError(ValueError):
    """txt/md를 UTF-8로 읽지 못했다."""


def detect_content_type(filename: str) -> str:
    """파일명 확장자로 문서 유형을 판별한다. 4종이 아니면 UnsupportedFileType."""
    content_type = PurePath(filename).suffix.removeprefix(".").lower()
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise UnsupportedFileType(f"지원하지 않는 파일 형식입니다: {content_type or '확장자 없음'}")
    return content_type


def extract_text(data: bytes, content_type: str) -> str:
    """파일 바이트에서 텍스트를 추출한다.

    PDF 페이지와 DOCX 문단은 빈 줄로 구분한다. 후속 청킹이 빈 줄을 문단 경계로
    사용하므로 파일 안의 구조가 추출 텍스트에도 남는다.

    Raises:
        UnsupportedFileType: `content_type`이 지원하는 네 유형이 아닐 때.
        TextDecodeError: TXT 또는 Markdown 바이트가 UTF-8이 아닐 때.
        ValueError: PDF 또는 DOCX 바이트가 비었거나 손상되었을 때.
    """
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise UnsupportedFileType(f"지원하지 않는 파일 형식입니다: {content_type}")

    if content_type in ("txt", "md"):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise TextDecodeError("텍스트 파일은 UTF-8 인코딩이어야 합니다.") from error

    try:
        if content_type == "pdf":
            pages = PdfReader(io.BytesIO(data)).pages
            return "\n\n".join(page.extract_text() or "" for page in pages)

        document = Document(io.BytesIO(data))
        return "\n\n".join(paragraph.text for paragraph in document.paragraphs)
    except Exception as error:
        raise ValueError(f"{content_type.upper()} 파일을 읽을 수 없습니다.") from error
