# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""앱 전체 응답에 붙는 보안 헤더.

정적 프론트·API·원격 MCP·원본 파일이 한 오리진에서 나가므로(ADR-041) 헤더도 한 곳에서
붙인다. 응답이 이미 정한 값(원본 파일의 CSP sandbox·nosniff)은 덮지 않는다.
"""

from io import BytesIO

import pytest
from conftest import upload_document
from fastapi.testclient import TestClient
from PIL import Image
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from openarchive.api.security_headers import SECURITY_HEADERS, SecurityHeaders

EXPECTED = {
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
}


def assert_security_headers(response) -> None:
    for name, value in EXPECTED.items():
        assert response.headers.get_list(name) == [value], name


def test_expected_header_set():
    """테스트의 기대값과 구현 상수가 따로 놀지 않게 묶는다."""
    assert {name.decode(): value.decode() for name, value in SECURITY_HEADERS} == EXPECTED


@pytest.mark.parametrize("path", ["/", "/search", "/documents/00000000-0000-0000-0000-000000000000"])
def test_static_pages(client: TestClient, path: str):
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_security_headers(response)


def test_api_success_and_error(client: TestClient):
    assert_security_headers(client.get("/api/health"))

    denied = client.get("/api/documents")
    assert denied.status_code == 401
    assert_security_headers(denied)


def test_remote_mcp(client: TestClient):
    """/mcp는 라우터가 아니라 ASGI 앱을 직접 붙인 경로다 — 미들웨어가 거기까지 닿는지 본다."""
    response = client.post("/mcp", json={})

    assert response.status_code == 401
    assert_security_headers(response)


def test_original_file_keeps_its_own_headers(db_client: TestClient):
    """원본 미리보기의 CSP sandbox를 지우거나 nosniff를 두 번 싣지 않는다."""
    image = BytesIO()
    Image.new("RGB", (10, 10), "white").save(image, format="PNG")
    created = upload_document(db_client, filename="그림.png", content=image.getvalue())
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]

    download = db_client.get(f"/api/documents/{document_id}/file")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")
    assert_security_headers(download)
    assert "content-security-policy" not in download.headers

    preview = db_client.get(f"/api/documents/{document_id}/files/1/preview")
    assert preview.status_code == 200
    assert preview.headers["content-disposition"].startswith("inline;")
    assert_security_headers(preview)
    assert preview.headers.get_list("content-security-policy") == ["sandbox; default-src 'none'"]


def test_header_already_set_by_response_wins():
    """응답이 정한 값을 미들웨어가 바꾸지 않는다 — 대소문자가 달라도 같은 헤더다."""

    def framed(request):
        return PlainTextResponse("ok", headers={"X-Frame-Options": "SAMEORIGIN"})

    app = SecurityHeaders(Starlette(routes=[Route("/", framed)]))
    response = TestClient(app).get("/")

    assert response.headers.get_list("x-frame-options") == ["SAMEORIGIN"]
    assert response.headers.get_list("referrer-policy") == ["same-origin"]
