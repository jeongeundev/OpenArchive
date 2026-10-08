"""원본 미리보기의 형식·열람·감사 경계를 실제 DB에서 검증한다."""

from io import BytesIO
from pathlib import Path

import pytest
from conftest import login_as, upload_document
from docx import Document
from PIL import Image
from test_audit import rows
from test_share_access import issue_share_token
from test_token_access import bearer, issue_token

FIXTURES = Path(__file__).parent / "fixtures"


def upload(client, filename, content, **kwargs):
    response = upload_document(client, filename=filename, content=content, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def pdf(client, **kwargs):
    content = (FIXTURES / "mixed_tax_pages.pdf").read_bytes()
    return upload(client, "세금.pdf", content, **kwargs), content


def test_pdf_preview_headers_bytes_and_audit(db_client, migrated_db):
    content = (FIXTURES / "mixed_tax_pages.pdf").read_bytes()
    doc = upload(db_client, "세금.txt", b"initial text")
    replacement = db_client.put(
        f"/api/documents/{doc}/file",
        files={"file": ("세금.pdf", content)},
        data={"current_version": "1"},
    )
    assert replacement.status_code == 200
    before = rows(migrated_db, doc)
    response = db_client.get(f"/api/documents/{doc}/files/2/preview")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert response.content == content
    assert rows(migrated_db, doc) == before + [
        ("original_previewed", "alice", "session", "세금", {"file_version": 2})
    ]


@pytest.mark.parametrize("extension", ["jpg", "jpeg", "JPG", "png"])
def test_image_preview(db_client, extension):
    if extension == "png":
        output = BytesIO()
        Image.new("RGB", (10, 10), "white").save(output, format="PNG")
        content = output.getvalue()
    else:
        content = (FIXTURES / "scan_tax_page1.jpg").read_bytes()
    doc = upload(db_client, f"image.{extension}", content)
    response = db_client.get(f"/api/documents/{doc}/files/1/preview")
    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "image/png" if extension == "png" else "image/jpeg"
    )
    assert response.content == content


@pytest.mark.parametrize("extension", ["docx", "txt"])
def test_unsupported_preview_has_no_audit(db_client, migrated_db, extension):
    content = b"text"
    if extension == "docx":
        document = Document()
        document.add_paragraph("text")
        output = BytesIO()
        document.save(output)
        content = output.getvalue()
    doc = upload(db_client, f"document.{extension}", content)
    before = rows(migrated_db, doc)
    response = db_client.get(f"/api/documents/{doc}/files/1/preview")
    assert response.status_code == 415
    assert response.json() == {"detail": "미리보기할 수 없는 형식입니다."}
    assert rows(migrated_db, doc) == before


def test_invisible_and_missing_version_have_no_audit(db_client, migrated_db):
    doc, _ = pdf(db_client, data={"visibility": "private"})
    before = rows(migrated_db, doc)
    login_as(db_client, "bob")
    response = db_client.get(f"/api/documents/{doc}/files/999/preview")
    assert response.status_code == 404
    assert response.json() == {"detail": "문서를 찾을 수 없습니다."}
    login_as(db_client, "alice")
    response = db_client.get(f"/api/documents/{doc}/files/999/preview")
    assert response.status_code == 404
    assert response.json() == {"detail": "원본 파일이 없습니다."}
    assert rows(migrated_db, doc) == before


def test_downloads_preserve_attachment_and_audit(db_client, migrated_db):
    doc, content = pdf(db_client)
    before = rows(migrated_db, doc)
    for path in ("file", "files/1"):
        response = db_client.get(f"/api/documents/{doc}/{path}")
        assert response.status_code == 200
        assert response.content == content
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "content-security-policy" not in response.headers
    assert rows(migrated_db, doc) == before + [
        ("original_downloaded", "alice", "session", "세금", {"file_version": 1})
    ] * 2


def test_share_cannot_preview_but_can_download(db_client, migrated_db):
    doc, _ = pdf(db_client)
    share = db_client.post("/api/shares", json={"name": "공유"}).json()["id"]
    assert db_client.put(f"/api/shares/{share}/documents/{doc}").status_code == 204
    token = issue_share_token(db_client, share)["token"]
    db_client.cookies.clear()
    before = rows(migrated_db, doc)
    response = db_client.get(f"/api/documents/{doc}/files/1/preview", headers=bearer(token))
    assert response.status_code == 403
    assert response.json() == {"detail": "공유 토큰으로는 열 수 없는 경로입니다."}
    assert rows(migrated_db, doc) == before
    assert db_client.get(f"/api/documents/{doc}/files/1", headers=bearer(token)).status_code == 200


def test_read_token_can_preview(db_client, migrated_db):
    doc, content = pdf(db_client)
    token = issue_token(db_client, "alice", scope="read")["token"]
    db_client.cookies.clear()
    response = db_client.get(f"/api/documents/{doc}/files/1/preview", headers=bearer(token))
    assert response.status_code == 200
    assert response.content == content
    assert rows(migrated_db, doc)[-1][0:3] == ("original_previewed", "alice", "token")
