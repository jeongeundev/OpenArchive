"""공유 토큰으로 REST를 연다 — 허용 목록의 읽기 경로만 (ADR-044 「공유」 결정 3·5, #97 c).

공유 토큰을 든 B사에게 A사의 DB는 공유 안 문서 5건만 있는 DB처럼 보여야 한다. 시나리오는
문서 100건 중 공유 5건이며, 관계·위키링크가 공유 안팎을 잇게 둬 "밖이 안 보인다"가 공허하지
않게 한다.
"""

import hashlib
from uuid import UUID

import psycopg
import pytest
from conftest import login_as, run_embedding_worker
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from openarchive.api.deps import current_user, require_reader
from openarchive.main import app

QUERY = "OpenSQL 공유 경계 문서"

# ADR-044 「공유」 결정 5의 허용 목록. 바꾸려면 ADR부터 바꾼다.
SHARE_READABLE_ROUTES = {
    ("POST", "/api/search"),
    ("GET", "/api/documents"),
    ("GET", "/api/documents/progress"),
    ("GET", "/api/documents/{document_id}"),
    ("GET", "/api/documents/{document_id}/file"),
    ("GET", "/api/documents/{document_id}/files/{file_version}"),
    ("GET", "/api/documents/{document_id}/links"),
    ("GET", "/api/documents/{document_id}/backlinks"),
    ("GET", "/api/documents/{document_id}/related"),
    ("GET", "/api/documents/{document_id}/versions/{version}"),
    ("GET", "/api/clusters"),
    ("GET", "/api/diagnostics"),
}

FORBIDDEN_DETAIL = "공유 토큰으로는 열 수 없는 경로입니다."


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def insert_document(conn, *, title, content, owner="alice", visibility="public", tags=()):
    return conn.execute(
        """
        INSERT INTO documents (title, content_type, content, content_hash, owner_id, visibility, tags)
        VALUES (%s, 'md', %s, %s, %s, %s, %s) RETURNING id
        """,
        (title, content, hashlib.sha256(content.encode()).hexdigest(), owner, visibility,
         list(tags)),
    ).fetchone()[0]


def add_edge(conn, src, dst, kind="related", score=0.8):
    conn.execute(
        """
        INSERT INTO document_edges (src_document_id, dst_document_id, kind, score)
        VALUES (%s, %s, %s, %s)
        """,
        (src, dst, kind, score),
    )


def issue_share_token(client: TestClient, share_id: str, name: str = "B사 연동") -> dict:
    response = client.post(f"/api/shares/{share_id}/tokens", json={"name": name})
    assert response.status_code == 201
    return response.json()


def collect_document_ids(value, known: set[str]) -> set[str]:
    """응답 JSON 어디에 있든 알려진 문서 id 문자열을 모은다."""
    found: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= collect_document_ids(item, known)
    elif isinstance(value, list):
        for item in value:
            found |= collect_document_ids(item, known)
    elif isinstance(value, str) and value in known:
        found.add(value)
    return found


@pytest.fixture
def scenario(db_client: TestClient, migrated_db: str):
    """문서 100건(alice·bob, 조직 공개·제한) 중 alice의 5건을 공유 S에 넣고 토큰을 발급한다.

    공유 4는 파일 업로드 문서라 원본이 있다. 관계는 워커가 만든 것을 지우고 손으로 둔다 —
    공유 0 → 외부 10, 외부 31 → 공유 2, 공유 0 ↔ 공유 1, 위키링크 공유 0 → 외부 50.
    """
    login_as(db_client, "alice")
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES ('bob', 'x') "
            "ON CONFLICT DO NOTHING"
        )
        outside = {}
        for i in range(5, 100):
            outside[i] = insert_document(
                conn,
                title=f"외부 {i}",
                content=f"{QUERY} 바깥 기록 {i}",
                owner="alice" if i % 2 else "bob",
                visibility="public" if i % 3 else "private",
                tags=["외부전용"],
            )
        specs = [
            ("공유 0", f"{QUERY} 공유 영 [[외부 50]] [[공유 1]]", "public"),
            ("공유 1", f"{QUERY} 공유 하나", "public"),
            ("공유 2", f"{QUERY} 공유 둘", "private"),
            ("공유 3", f"{QUERY} 공유 셋", "private"),
        ]
        inside = [
            insert_document(conn, title=title, content=content, visibility=visibility,
                            tags=["공유"])
            for title, content, visibility in specs
        ]

    uploaded = db_client.post(
        "/api/documents",
        files={"file": ("공유 4.txt", f"{QUERY} 공유 넷".encode(), "text/plain")},
        data={"visibility": "public"},
    )
    assert uploaded.status_code == 201
    inside.append(UUID(uploaded.json()["id"]))

    run_embedding_worker(migrated_db)
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM document_edges")
        add_edge(conn, inside[0], inside[1])
        add_edge(conn, inside[0], outside[10])
        add_edge(conn, inside[3], outside[20], kind="overlaps", score=1.0)
        add_edge(conn, outside[31], inside[2])

    share = db_client.post("/api/shares", json={"name": "B사"}).json()
    for document_id in inside:
        assert db_client.put(
            f"/api/shares/{share['id']}/documents/{document_id}"
        ).status_code == 204
    token = issue_share_token(db_client, share["id"])

    inside_ids = {str(i) for i in inside}
    all_ids = inside_ids | {str(i) for i in outside.values()}
    return {
        "share": share,
        "token": token,
        "inside": [str(i) for i in inside],
        "outside": {k: str(v) for k, v in outside.items()},
        "inside_ids": inside_ids,
        "all_ids": all_ids,
    }


def test_the_user_session_sees_outside_the_share(db_client: TestClient, scenario):
    """같은 데이터에서 alice는 공유 밖 관계·링크·검색 결과를 본다 — 아래 단언이 공허하지 않다."""
    inside, outside, all_ids = scenario["inside"], scenario["outside"], scenario["all_ids"]

    related = db_client.get(f"/api/documents/{inside[0]}/related").json()
    links = db_client.get(f"/api/documents/{inside[0]}/links").json()
    backlinks = db_client.get(f"/api/documents/{inside[2]}/related").json()
    search = db_client.post("/api/search", json={"query": QUERY}).json()

    assert outside[10] in collect_document_ids(related, all_ids)
    assert outside[50] in collect_document_ids(links, all_ids)
    assert outside[31] in collect_document_ids(backlinks, all_ids)
    assert collect_document_ids(search, all_ids) - scenario["inside_ids"]
    # bob의 제한 문서는 alice에게도 없다 — 100건이 아니라 공유보다 넓다는 것만 본다.
    assert len(db_client.get("/api/documents").json()) > 5


def test_a_share_token_sees_only_the_shared_documents(db_client: TestClient, scenario):
    headers = bearer(scenario["token"]["token"])
    inside_ids, all_ids = scenario["inside_ids"], scenario["all_ids"]

    search = db_client.post("/api/search", json={"query": QUERY}, headers=headers)
    listed = db_client.get("/api/documents", headers=headers)
    progress = db_client.get("/api/documents/progress", headers=headers)

    assert search.status_code == 200
    found = collect_document_ids(search.json(), all_ids)
    assert found and found <= inside_ids
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()} == inside_ids
    assert progress.status_code == 200
    assert sum(progress.json().values()) == 5


@pytest.mark.parametrize(
    "path",
    [
        "/api/documents/{id}",
        "/api/documents/{id}/links",
        "/api/documents/{id}/backlinks",
        "/api/documents/{id}/related",
        "/api/documents/{id}/versions/1",
    ],
)
def test_reads_inside_the_share_never_mention_outside(db_client: TestClient, scenario, path):
    headers = bearer(scenario["token"]["token"])
    inside_ids, all_ids = scenario["inside_ids"], scenario["all_ids"]

    for document_id in scenario["inside"]:
        response = db_client.get(path.format(id=document_id), headers=headers)
        assert response.status_code == 200, (path, response.text)
        assert collect_document_ids(response.json(), all_ids) <= inside_ids


def test_the_original_file_of_a_shared_document_downloads(db_client: TestClient, scenario):
    headers = bearer(scenario["token"]["token"])
    uploaded = scenario["inside"][4]

    latest = db_client.get(f"/api/documents/{uploaded}/file", headers=headers)
    first = db_client.get(f"/api/documents/{uploaded}/files/1", headers=headers)

    assert latest.status_code == 200
    assert first.status_code == 200
    assert latest.content == f"{QUERY} 공유 넷".encode()


@pytest.mark.parametrize("which", [10, 31, 50, 60])
@pytest.mark.parametrize(
    "path",
    [
        "/api/documents/{id}",
        "/api/documents/{id}/links",
        "/api/documents/{id}/backlinks",
        "/api/documents/{id}/related",
        "/api/documents/{id}/versions/1",
        "/api/documents/{id}/file",
        "/api/documents/{id}/files/1",
    ],
)
def test_documents_outside_the_share_do_not_exist(db_client: TestClient, scenario, path, which):
    """외부 10·31·50은 관계·링크로 이어진 조직 공개 문서, 외부 60은 제한 문서다."""
    headers = bearer(scenario["token"]["token"])

    response = db_client.get(path.format(id=scenario["outside"][which]), headers=headers)

    assert response.status_code == 404


def test_aggregates_stay_inside_the_share(db_client: TestClient, scenario):
    headers = bearer(scenario["token"]["token"])
    inside_ids, all_ids = scenario["inside_ids"], scenario["all_ids"]

    clusters = db_client.get("/api/clusters", headers=headers)
    diagnostics = db_client.get("/api/diagnostics", headers=headers)

    assert clusters.status_code == 200
    assert collect_document_ids(clusters.json(), all_ids) <= inside_ids
    assert sum(cluster["size"] for cluster in clusters.json()["clusters"]) == 5
    assert diagnostics.status_code == 200
    assert collect_document_ids(diagnostics.json(), all_ids) <= inside_ids
    broken = {link["target_title"] for link in diagnostics.json()["broken_links"]["items"]}
    assert "외부 50" in broken


def db_snapshot(dsn: str):
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            """
            SELECT (SELECT count(*) FROM documents),
                   (SELECT count(*) FROM document_versions),
                   (SELECT string_agg(id::text || array_to_string(tags, ',') || visibility, '|'
                                      ORDER BY id) FROM documents),
                   (SELECT count(*) FROM shares),
                   (SELECT count(*) FROM document_grants),
                   (SELECT count(*) FROM api_tokens),
                   (SELECT count(*) FROM users),
                   (SELECT count(*) FROM groups)
            """
        ).fetchone()


def forbidden_requests(scenario):
    document_id = scenario["inside"][0]
    share_id = scenario["share"]["id"]
    return [
        ("GET", "/api/principals", None),
        ("GET", "/api/system/status", None),
        ("GET", "/api/auth/me", None),
        ("GET", f"/api/documents/{document_id}/access", None),
        ("GET", f"/api/documents/{document_id}/tag-suggestions", None),
        ("POST", "/api/documents/text",
         {"title": "침입", "content": "공유 토큰이 쓴 문서", "content_type": "md"}),
        ("PUT", f"/api/documents/{document_id}/tags", {"tags": ["침입"]}),
        ("PUT", f"/api/documents/{document_id}", {"content": "바뀜", "version": 1}),
        ("PUT", f"/api/documents/{document_id}/access",
         {"visibility": "public", "users": [], "groups": []}),
        ("POST", f"/api/documents/{document_id}/versions/1/restore", {"current_version": 1}),
        ("POST", f"/api/documents/{document_id}/reembed", None),
        ("DELETE", f"/api/documents/{document_id}", None),
        ("GET", "/api/shares", None),
        ("POST", "/api/shares", {"name": "공유가 만든 공유"}),
        ("DELETE", f"/api/shares/{share_id}", None),
        ("POST", f"/api/shares/{share_id}/tokens", {"name": "재생"}),
        ("GET", "/api/auth/tokens", None),
        ("POST", "/api/auth/tokens", {"name": "재생", "scope": "read"}),
        ("GET", "/api/admin/users", None),
        ("POST", "/api/admin/users", {"username": "mallory", "password": "x"}),
        ("GET", "/api/admin/groups", None),
        ("POST", "/api/admin/groups", {"name": "침입"}),
    ]


def test_paths_outside_the_allow_list_are_403(
    db_client: TestClient, migrated_db: str, scenario
):
    headers = bearer(scenario["token"]["token"])
    before = db_snapshot(migrated_db)

    for method, path, body in forbidden_requests(scenario):
        response = db_client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403, (method, path, response.status_code, response.text)

    assert db_snapshot(migrated_db) == before


def test_principals_detail_names_the_share_boundary(db_client: TestClient, scenario):
    response = db_client.get("/api/principals", headers=bearer(scenario["token"]["token"]))

    assert response.status_code == 403
    assert response.json()["detail"] == FORBIDDEN_DETAIL


def test_a_revoked_share_token_is_anonymous(db_client: TestClient, scenario):
    token = scenario["token"]
    share_id = scenario["share"]["id"]

    assert db_client.delete(f"/api/shares/{share_id}/tokens/{token['id']}").status_code == 204

    assert db_client.get("/api/documents", headers=bearer(token["token"])).status_code == 401


def test_the_token_of_a_deleted_share_is_anonymous(db_client: TestClient, scenario):
    token = scenario["token"]

    assert db_client.delete(f"/api/shares/{scenario['share']['id']}").status_code == 204

    assert db_client.get("/api/documents", headers=bearer(token["token"])).status_code == 401


def test_deleting_the_share_owner_invalidates_the_token(
    db_client: TestClient, migrated_db: str, scenario
):
    login_as(db_client, "dave")
    share = db_client.post("/api/shares", json={"name": "dave의 공유"}).json()
    token = issue_share_token(db_client, share["id"])
    headers = bearer(token["token"])
    assert db_client.get("/api/documents", headers=headers).status_code == 200

    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM users WHERE username = 'dave'")

    assert db_client.get("/api/documents", headers=headers).status_code == 401


def depends_on(dependant, target) -> bool:
    return any(
        dependency.call is target or depends_on(dependency, target)
        for dependency in dependant.dependencies
    )


def api_routes():
    """앱의 모든 API 경로를 (경로, 메서드 집합, dependant)로 펼친다.

    FastAPI 0.14x는 include_router를 감싸 두므로 감싼 라우터의 실효 경로까지 내려간다.
    """
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route.path, route.methods, route.dependant
        elif hasattr(route, "effective_route_contexts"):
            for context in route.effective_route_contexts():
                if context.methods:
                    yield context.path, context.methods, context.dependant


def test_only_the_allow_list_accepts_a_share_principal():
    """새 경로는 기본적으로 공유를 막는다. 공유를 열려면 이 집합과 ADR을 함께 바꾼다."""
    routes = list(api_routes())
    readable = {
        (method, path)
        for path, methods, dependant in routes
        if depends_on(dependant, require_reader)
        for method in methods
    }

    # 펼치기가 실제로 경로를 찾았는지 — 빈 집합끼리 같아지는 공허한 통과를 막는다.
    assert ("GET", "/api/principals") in {(m, p) for p, ms, _ in routes for m in ms}
    assert readable == SHARE_READABLE_ROUTES


def test_only_auth_me_reads_the_principal_without_a_guard():
    """current_user를 직접 받는 경로는 공유 주체를 그대로 통과시킨다 — 스스로 막는 /me 하나뿐이다.

    위 허용 목록 단언은 require_reader 쪽만 본다. 이 단언이 없으면 current_user를 직접 쓰는
    새 경로가 허용 목록 밖에서 공유를 여는데도 테스트가 초록으로 남는다.
    """
    direct = {
        (method, path)
        for path, methods, dependant in api_routes()
        if any(dependency.call is current_user for dependency in dependant.dependencies)
        for method in methods
    }

    assert direct == {("GET", "/api/auth/me")}
