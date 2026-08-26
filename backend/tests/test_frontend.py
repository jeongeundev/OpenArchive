"""빌드된 프론트를 API와 같은 오리진에서 서빙한다 (ADR-041).

정적 export는 문서 상세처럼 실행 중에 생기는 경로를 만들 수 없어, 껍데기 하나를
뽑아 두고 서버가 모든 문서 ID 요청에 그것을 내려준다. 그 규칙과 경로 안전성이
여기서 검증하는 대상이다.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend import FALLBACK_SEGMENT, mount_frontend


@pytest.fixture
def built(tmp_path: Path) -> Path:
    """`STATIC_EXPORT=1 next build`가 내놓는 모양을 그대로 흉내 낸 산출물."""
    (tmp_path / "_next" / "static").mkdir(parents=True)
    (tmp_path / "_next" / "static" / "app.js").write_text("console.log(1)", "utf-8")
    (tmp_path / "index.html").write_text("<html>목록</html>", "utf-8")
    (tmp_path / "search.html").write_text("<html>검색</html>", "utf-8")
    (tmp_path / "404.html").write_text("<html>없음</html>", "utf-8")
    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / f"{FALLBACK_SEGMENT}.html").write_text("<html>문서 상세</html>", "utf-8")
    (documents / f"{FALLBACK_SEGMENT}.txt").write_text("RSC payload", "utf-8")
    (documents / FALLBACK_SEGMENT).mkdir()
    (documents / FALLBACK_SEGMENT / "__next._tree.txt").write_text("tree", "utf-8")
    return tmp_path


@pytest.fixture
def client(built: Path) -> TestClient:
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    assert mount_frontend(app, built) is True
    return TestClient(app)


def test_api_routes_still_win(client: TestClient):
    """정적 서빙이 API를 가리면 안 된다. catch-all은 마지막에만 잡아야 한다."""
    assert client.get("/api/health").json() == {"status": "ok"}


def test_serves_the_index_and_named_pages(client: TestClient):
    assert client.get("/").text == "<html>목록</html>"
    # 정적 export는 /search를 search.html로 내놓는다. 확장자 없는 요청이 그것을 찾아야 한다.
    assert client.get("/search").text == "<html>검색</html>"


def test_serves_assets(client: TestClient):
    assert client.get("/_next/static/app.js").text == "console.log(1)"


def test_any_document_id_falls_back_to_the_placeholder_page(client: TestClient):
    """문서 ID는 실행 중에 생기므로 빌드가 만들 수 없다. 껍데기가 모든 ID를 받는다."""
    assert client.get("/documents/8f14e45f-ceea-467a-9c1b-3d4a2b1e0000").text == (
        "<html>문서 상세</html>"
    )


def test_fallback_keeps_the_suffix_for_client_navigation(client: TestClient):
    """`<Link>` 이동은 HTML이 아니라 RSC 페이로드를 받아 간다.

    확장자를 잃고 .html로 떨어지면 라우터가 페이로드 대신 HTML을 읽어 이동이 깨진다.
    """
    assert client.get("/documents/any-id.txt").text == "RSC payload"
    assert client.get("/documents/any-id/__next._tree.txt").text == "tree"


def test_link_prefetch_uses_head(client: TestClient):
    """Next의 `<Link>` 프리페치는 HEAD로 온다 (실측).

    GET만 받으면 페이지를 여는 것만으로 콘솔이 405로 뒤덮인다 — 화면은 멀쩡히
    동작하므로 브라우저를 열어보기 전까지 드러나지 않는다.
    """
    assert client.head("/search").status_code == 200
    assert client.head("/documents/any-id").status_code == 200


def test_unknown_paths_get_the_404_page(client: TestClient):
    response = client.get("/이런/경로는/없다")
    assert response.status_code == 404
    assert response.text == "<html>없음</html>"


@pytest.mark.parametrize(
    "path",
    ["/../../../etc/passwd", "/documents/../../etc/passwd", "/%2e%2e%2f%2e%2e%2fetc%2fpasswd"],
)
def test_refuses_to_escape_the_static_directory(client: TestClient, path: str):
    """경로 조작으로 산출물 바깥을 읽을 수 없어야 한다."""
    response = client.get(path)
    assert response.status_code == 404
    assert "root:" not in response.text


def test_reports_when_there_is_nothing_to_serve(tmp_path: Path):
    """개발 중에는 산출물이 없다. 그때는 API만 뜨고 아무 라우트도 더하지 않는다."""
    app = FastAPI()
    assert mount_frontend(app, tmp_path / "없는-디렉토리") is False
    assert TestClient(app).get("/").status_code == 404
