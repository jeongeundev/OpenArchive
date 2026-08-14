import hashlib
import io
import zipfile
from uuid import uuid4

import psycopg
from conftest import login_as, run_embedding_worker
from conftest import upload_document as upload
from docx import Document
from fastapi.testclient import TestClient


def edit(client: TestClient, document_id: str, *, content: str, version: int, user_id="alice"):
    if user_id is not None:
        login_as(client, user_id)
    return client.put(
        f"/api/documents/{document_id}",
        json={"content": content, "version": version},
    )


def replace_tags(client: TestClient, document_id: str, tags: list[str], user_id="alice"):
    if user_id is not None:
        login_as(client, user_id)
    return client.put(
        f"/api/documents/{document_id}/tags",
        json={"tags": tags},
    )


def test_upload_txt_returns_pending_document(db_client: TestClient):
    response = upload(db_client)

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "guide"
    assert body["filename"] == "guide.txt"
    assert body["embedding_status"] == "pending"
    assert body["owner_id"] == "alice"


def test_upload_trigger_creates_job_and_initial_text_version(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client).json()["id"]

    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT status FROM embedding_jobs WHERE document_id = %s", (document_id,)
        ).fetchone() == ("pending",)
        assert conn.execute(
            "SELECT version FROM document_versions WHERE document_id = %s", (document_id,)
        ).fetchone() == (1,)


def test_text_ingest_matches_upload_pipeline_derivatives(
    db_client: TestClient, migrated_db: str
):
    upload(
        db_client,
        filename="target.md",
        content=b"target",
        data={"title": "Target"},
    )
    content = "shared pipeline text [[Target]]"
    uploaded = upload(
        db_client,
        filename="uploaded.md",
        content=content.encode(),
        data={"title": "Uploaded"},
    )
    supplied = db_client.post(
        "/api/documents/text",
        json={"title": "Supplied", "content": content},
    )

    assert uploaded.status_code == 201
    assert supplied.status_code == 201
    supplied_body = supplied.json()
    assert supplied_body["filename"] is None
    run_embedding_worker(migrated_db)

    document_ids = [uploaded.json()["id"], supplied_body["id"]]
    with psycopg.connect(migrated_db) as conn:
        derivatives = []
        for document_id in document_ids:
            derivatives.append(
                (
                    conn.execute(
                        "SELECT count(*) FROM embedding_jobs WHERE document_id = %s",
                        (document_id,),
                    ).fetchone()[0],
                    conn.execute(
                        "SELECT count(*) FROM document_versions WHERE document_id = %s",
                        (document_id,),
                    ).fetchone()[0],
                    conn.execute(
                        "SELECT count(*) FROM document_chunks WHERE document_id = %s",
                        (document_id,),
                    ).fetchone()[0],
                    conn.execute(
                        "SELECT array_agg(target_title ORDER BY target_title) "
                        "FROM document_links WHERE src_document_id = %s",
                        (document_id,),
                    ).fetchone()[0],
                    conn.execute(
                        "SELECT count(*) FROM document_edges WHERE src_document_id = %s",
                        (document_id,),
                    ).fetchone()[0]
                    > 0,
                )
            )

    assert derivatives[0] == derivatives[1]


def test_text_ingest_uses_authenticated_owner_and_normalizes_metadata(
    db_client: TestClient,
):
    login_as(db_client, "alice")
    response = db_client.post(
        "/api/documents/text",
        json={
            "title": "API document",
            "content": "document text",
            "content_type": "txt",
            "tags": [" alpha ", "beta", "alpha", "  "],
            "visibility": "private",
            "owner_id": "mallory",
        },
    )

    assert response.status_code == 201
    assert response.json()["filename"] is None
    assert response.json()["content_type"] == "txt"
    assert response.json()["tags"] == ["alpha", "beta"]
    assert response.json()["visibility"] == "private"
    assert response.json()["owner_id"] == "alice"


def test_text_ingest_requires_authentication(db_client: TestClient):
    response = db_client.post(
        "/api/documents/text",
        json={"title": "Anonymous", "content": "document text"},
    )

    assert response.status_code == 401


def test_private_text_ingest_is_hidden_from_other_users(db_client: TestClient):
    login_as(db_client, "alice")
    document_id = db_client.post(
        "/api/documents/text",
        json={
            "title": "Private API document",
            "content": "private text",
            "visibility": "private",
        },
    ).json()["id"]

    login_as(db_client, "bob")
    assert all(item["id"] != document_id for item in db_client.get("/api/documents").json())
    assert db_client.get(f"/api/documents/{document_id}").status_code == 404


def test_text_ingest_rejects_invalid_content_without_saving(
    db_client: TestClient, migrated_db: str
):
    login_as(db_client, "alice")

    for content in (" \t\r\n\f", "x" * 500_001):
        response = db_client.post(
            "/api/documents/text",
            json={"title": "Invalid", "content": content},
        )
        assert response.status_code == 400

    unsupported = db_client.post(
        "/api/documents/text",
        json={"title": "PDF", "content": "text", "content_type": "pdf"},
    )
    assert unsupported.status_code == 422
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (0,)


def test_text_ingest_document_uses_existing_optimistic_locking(db_client: TestClient):
    login_as(db_client, "alice")
    created = db_client.post(
        "/api/documents/text",
        json={"title": "Editable", "content": "version one"},
    ).json()

    updated = edit(
        db_client,
        created["id"],
        content="version two",
        version=created["version"],
    )
    stale = edit(
        db_client,
        created["id"],
        content="stale edit",
        version=created["version"],
    )

    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert stale.status_code == 409
    assert stale.json()["current_version"] == 2


def test_document_link_and_backlink_endpoints_return_resolved_documents(
    db_client: TestClient,
):
    target_id = upload(
        db_client,
        filename="target.md",
        content=b"target",
        data={"title": "Target"},
    ).json()["id"]
    source_id = upload(
        db_client,
        filename="source.md",
        content=b"[[Target]] and [[Missing]]",
        data={"title": "Source"},
    ).json()["id"]

    links = db_client.get(f"/api/documents/{source_id}/links")
    backlinks = db_client.get(f"/api/documents/{target_id}/backlinks")

    assert links.status_code == 200
    assert links.json() == [
        {"title": "Missing", "document_id": None},
        {"title": "Target", "document_id": target_id},
    ]
    assert backlinks.status_code == 200
    assert backlinks.json() == [{"document_id": source_id, "title": "Source"}]


def test_upload_rejects_blank_text_without_saving(db_client: TestClient, migrated_db: str):
    response = upload(db_client, content=b" \t\r\n\f")

    assert response.status_code == 400
    assert response.json() == {
        "detail": "문서에서 텍스트를 추출하지 못했습니다. 스캔 이미지 PDF는 지원하지 않습니다."
    }
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (0,)


def test_upload_rejects_unsupported_extension(db_client: TestClient):
    response = upload(db_client, filename="document.hwp")

    assert response.status_code == 400
    assert "pdf, docx, txt, md" in response.json()["detail"]


def test_upload_rejects_non_utf8_text(db_client: TestClient):
    response = upload(db_client, content="한글".encode("cp949"))

    assert response.status_code == 400
    assert response.json()["detail"] == "텍스트 파일은 UTF-8 인코딩이어야 합니다."


def test_oversized_upload_is_rejected_before_read(db_client: TestClient, monkeypatch):
    async def fail_if_read(*args, **kwargs):
        raise AssertionError("oversized upload must be rejected before read()")

    monkeypatch.setattr("starlette.datastructures.UploadFile.read", fail_if_read)

    response = upload(db_client, content=b"x" * (10_000_000 + 1))

    assert response.status_code == 413


def test_upload_larger_than_its_declared_size_is_rejected(
    db_client: TestClient, monkeypatch
):
    """`file.size`는 클라이언트가 보내는 선언값이라 없거나 실제와 다를 수 있다.

    선언값만 보고 통과시키면 크기를 안 보내거나 줄여 보내는 클라이언트에게는 상한이
    사실상 없다. 위 검사는 큰 파일을 읽지 않기 위한 것이고, 경계 자체는 읽어들인
    바이트로도 지켜져야 한다.
    """
    oversized = b"x" * (10_000_000 + 1)

    async def read_oversized(self, size: int = -1) -> bytes:
        return oversized

    monkeypatch.setattr("starlette.datastructures.UploadFile.read", read_oversized)

    response = upload(db_client, content=b"small")

    assert response.status_code == 413


def test_extracted_text_over_service_limit_is_rejected(db_client: TestClient):
    response = upload(db_client, content=b"x" * 500_001)

    assert response.status_code == 400
    assert "500KB" in response.json()["detail"]


def test_upload_near_file_limit_with_small_extracted_text_succeeds(db_client: TestClient):
    buf = io.BytesIO()
    document = Document()
    document.add_paragraph("OpenSQL near-limit document")
    document.save(buf)
    with zipfile.ZipFile(buf, "a", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("word/media/padding.bin", b"x" * 9_000_000)

    response = upload(db_client, filename="near-limit.docx", content=buf.getvalue())

    assert len(buf.getvalue()) < 10_000_000
    assert response.status_code == 201


def test_upload_requires_user_id(db_client: TestClient):
    response = upload(db_client, user_id=None)

    assert response.status_code == 401


def test_upload_stores_sha256_of_extracted_text(db_client: TestClient, migrated_db: str):
    content = "해시 기준 텍스트"
    document_id = upload(db_client, content=content.encode()).json()["id"]

    with psycopg.connect(migrated_db) as conn:
        (stored_hash,) = conn.execute(
            "SELECT content_hash FROM documents WHERE id = %s", (document_id,)
        ).fetchone()

    assert stored_hash == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_list_hides_other_users_private_documents_and_requires_login(
    db_client: TestClient,
):
    public_id = upload(db_client, data={"visibility": "public"}).json()["id"]
    private_id = upload(
        db_client, filename="private.txt", data={"visibility": "private"}
    ).json()["id"]

    login_as(db_client, "bob")
    bob_ids = {item["id"] for item in db_client.get("/api/documents").json()}
    db_client.post("/api/auth/logout")
    # ADR-028: 익명은 아무 문서도 보지 못한다. 제거된 헤더로도 열리지 않는다.
    anonymous = db_client.get("/api/documents", headers={"X-User-Id": "alice"})

    assert public_id in bob_ids
    assert private_id not in bob_ids
    assert anonymous.status_code == 401


def test_list_filters_by_tag_and_status(db_client: TestClient):
    matching_id = upload(
        db_client, data={"tags": "database", "visibility": "public"}
    ).json()["id"]
    upload(db_client, filename="other.txt", data={"tags": "manual"})

    response = db_client.get("/api/documents", params={"tag": "database", "status": "pending"})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [matching_id]
    assert "content" not in response.json()[0]


def test_detail_reports_versions_and_chunk_state_before_and_after_embedding(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client).json()["id"]

    before = db_client.get(f"/api/documents/{document_id}").json()
    assert before["content"] == "OpenSQL guide"
    assert [version["version"] for version in before["versions"]] == [1]
    assert before["chunk_count"] == 0
    assert before["chunk_version"] is None

    run_embedding_worker(migrated_db)

    after = db_client.get(f"/api/documents/{document_id}").json()
    assert after["chunk_count"] > 0
    assert after["chunk_version"] == 1


def test_detail_hides_other_users_private_document(db_client: TestClient):
    document_id = upload(db_client, data={"visibility": "private"}).json()["id"]

    login_as(db_client, "bob")
    response = db_client.get(f"/api/documents/{document_id}")

    assert response.status_code == 404


def test_detail_returns_404_for_missing_document(db_client: TestClient):
    login_as(db_client, "alice")

    response = db_client.get(f"/api/documents/{uuid4()}")

    assert response.status_code == 404


def test_owner_can_edit_extracted_text_as_a_new_version(db_client: TestClient):
    document_id = upload(db_client).json()["id"]

    response = edit(db_client, document_id, content="Updated extracted text", version=1)

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "Updated extracted text"
    assert body["version"] == 2


def test_owner_can_replace_tags_and_detail_reflects_them(db_client: TestClient):
    document_id = upload(db_client, data={"tags": "old"}).json()["id"]

    response = replace_tags(db_client, document_id, ["database", "manual"])

    assert response.status_code == 200
    assert response.json()["tags"] == ["database", "manual"]
    assert db_client.get(f"/api/documents/{document_id}").json()["tags"] == [
        "database",
        "manual",
    ]


def test_replacing_tags_does_not_trigger_text_versioning_or_reembedding(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client).json()["id"]
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE embedding_jobs SET status = 'done' WHERE document_id = %s",
            (document_id,),
        )
        conn.execute(
            "UPDATE documents SET embedding_status = 'ready' WHERE id = %s",
            (document_id,),
        )

    assert replace_tags(db_client, document_id, ["database"]).status_code == 200

    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT version, embedding_status FROM documents WHERE id = %s",
            (document_id,),
        ).fetchone() == (1, "ready")
        assert conn.execute(
            "SELECT count(*) FROM embedding_jobs WHERE document_id = %s AND status = 'pending'",
            (document_id,),
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM document_versions WHERE document_id = %s",
            (document_id,),
        ).fetchone() == (1,)


def test_replacing_tags_strips_blanks_deduplicates_and_preserves_order(
    db_client: TestClient,
):
    document_id = upload(db_client).json()["id"]

    response = replace_tags(
        db_client,
        document_id,
        [" database ", "", "manual", "database", "  ", "Manual"],
    )

    assert response.status_code == 200
    assert response.json()["tags"] == ["database", "manual", "Manual"]


def test_replacing_tags_with_empty_array_removes_all_tags(db_client: TestClient):
    document_id = upload(db_client, data={"tags": "old"}).json()["id"]

    response = replace_tags(db_client, document_id, [])

    assert response.status_code == 200
    assert response.json()["tags"] == []


def test_upload_normalizes_tags_stripping_blanks_and_duplicates(db_client: TestClient):
    """업로드 경로도 태그 교체와 같은 정규화를 거친다 — 공백 제거·순서 보존 중복 제거."""
    response = upload(db_client, data={"tags": [" 규정 ", "규정", "  ", "인사"]})

    assert response.status_code == 201
    assert response.json()["tags"] == ["규정", "인사"]

    detail = db_client.get(f"/api/documents/{response.json()['id']}")
    assert detail.json()["tags"] == ["규정", "인사"]


def test_upload_without_tags_stores_empty_list(db_client: TestClient):
    response = upload(db_client)

    assert response.status_code == 201
    assert response.json()["tags"] == []


def test_edit_trigger_creates_version_job_and_pending_status(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client).json()["id"]
    with psycopg.connect(migrated_db) as conn:
        conn.execute("UPDATE embedding_jobs SET status = 'done' WHERE document_id = %s", (document_id,))
        conn.execute("UPDATE documents SET embedding_status = 'ready' WHERE id = %s", (document_id,))

    assert edit(db_client, document_id, content="version two", version=1).status_code == 200

    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT version FROM document_versions WHERE document_id = %s ORDER BY version",
            (document_id,),
        ).fetchall() == [(1,), (2,)]
        assert conn.execute(
            "SELECT embedding_status FROM documents WHERE id = %s", (document_id,)
        ).fetchone() == ("pending",)
        assert conn.execute(
            "SELECT count(*) FROM embedding_jobs WHERE document_id = %s AND status = 'pending'",
            (document_id,),
        ).fetchone() == (1,)


def test_stale_edit_returns_current_version_without_changing_document(
    db_client: TestClient,
):
    document_id = upload(db_client).json()["id"]
    assert edit(db_client, document_id, content="version two", version=1).status_code == 200

    response = edit(db_client, document_id, content="stale overwrite", version=1)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요.",
        "current_version": 2,
    }
    detail = db_client.get(f"/api/documents/{document_id}").json()
    assert (detail["content"], detail["version"]) == ("version two", 2)


def test_edit_rejects_blank_content_without_changing_document(db_client: TestClient):
    document_id = upload(db_client).json()["id"]

    response = edit(db_client, document_id, content=" \n\t", version=1)

    assert response.status_code == 400
    detail = db_client.get(f"/api/documents/{document_id}").json()
    assert (detail["content"], detail["version"]) == ("OpenSQL guide", 1)


def test_edit_rejects_extracted_text_over_service_limit(db_client: TestClient):
    document_id = upload(db_client).json()["id"]

    response = edit(db_client, document_id, content="x" * 500_001, version=1)

    assert response.status_code == 400
    assert "500KB" in response.json()["detail"]


def test_edit_keeps_old_chunks_until_worker_replaces_them_and_exposes_convergence(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client).json()["id"]

    run_embedding_worker(migrated_db)
    assert edit(db_client, document_id, content="new searchable text", version=1).status_code == 200

    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT DISTINCT version FROM document_chunks WHERE document_id = %s", (document_id,)
        ).fetchall() == [(1,)]
        assert conn.execute(
            """SELECT count(DISTINCT d.id) FROM documents d JOIN document_chunks c
               ON c.document_id = d.id WHERE c.version <> d.version"""
        ).fetchone() == (1,)

    run_embedding_worker(migrated_db)
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            """SELECT count(DISTINCT d.id) FROM documents d JOIN document_chunks c
               ON c.document_id = d.id WHERE c.version <> d.version"""
        ).fetchone() == (0,)


def test_delete_cascades_all_document_rows(db_client: TestClient, migrated_db: str):
    document_id = upload(db_client).json()["id"]

    run_embedding_worker(migrated_db)
    with psycopg.connect(migrated_db) as conn:
        for table in ("document_chunks", "document_versions", "embedding_jobs"):
            assert conn.execute(
                f"SELECT count(*) FROM {table} WHERE document_id = %s", (document_id,)
            ).fetchone()[0] > 0

    response = db_client.delete(f"/api/documents/{document_id}")

    assert response.status_code == 204
    assert db_client.get(f"/api/documents/{document_id}").status_code == 404
    with psycopg.connect(migrated_db) as conn:
        for table in ("document_chunks", "document_versions", "embedding_jobs"):
            assert conn.execute(
                f"SELECT count(*) FROM {table} WHERE document_id = %s", (document_id,)
            ).fetchone() == (0,)


def test_reembed_recovers_error_without_changing_version_and_coalesces_requests(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client).json()["id"]
    with psycopg.connect(migrated_db) as conn:
        conn.execute("UPDATE embedding_jobs SET status = 'error' WHERE document_id = %s", (document_id,))
        conn.execute("UPDATE documents SET embedding_status = 'error' WHERE id = %s", (document_id,))

    for _ in range(2):
        response = db_client.post(f"/api/documents/{document_id}/reembed")
        assert response.status_code == 200

    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT version, embedding_status FROM documents WHERE id = %s", (document_id,)
        ).fetchone() == (1, "pending")
        assert conn.execute(
            "SELECT count(*) FROM document_versions WHERE document_id = %s", (document_id,)
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM embedding_jobs WHERE document_id = %s AND status = 'pending'",
            (document_id,),
        ).fetchone() == (1,)

    run_embedding_worker(migrated_db)
    detail = db_client.get(f"/api/documents/{document_id}").json()
    assert detail["embedding_status"] == "ready"
    assert detail["chunk_count"] > 0


def test_write_endpoints_share_visibility_aware_ownership_rules(db_client: TestClient):
    private_id = upload(db_client, data={"visibility": "private"}).json()["id"]
    public_id = upload(db_client, filename="public.txt", data={"visibility": "public"}).json()["id"]

    requests = (
        lambda document_id: db_client.put(
            f"/api/documents/{document_id}", json={"content": "x", "version": 1}
        ),
        lambda document_id: db_client.delete(f"/api/documents/{document_id}"),
        lambda document_id: db_client.post(f"/api/documents/{document_id}/reembed"),
        lambda document_id: db_client.put(
            f"/api/documents/{document_id}/tags",
            json={"tags": ["database"]},
        ),
    )
    login_as(db_client, "bob")
    for request in requests:
        assert request(private_id).status_code == 404
        assert request(public_id).status_code == 403
        db_client.post("/api/auth/logout")
        assert request(public_id).status_code == 401
        login_as(db_client, "bob")


def test_write_endpoints_return_404_for_missing_document(db_client: TestClient):
    missing_id = str(uuid4())
    login_as(db_client, "alice")

    assert db_client.put(
        f"/api/documents/{missing_id}",
        json={"content": "x", "version": 1},
    ).status_code == 404
    assert db_client.delete(f"/api/documents/{missing_id}").status_code == 404
    assert db_client.post(f"/api/documents/{missing_id}/reembed").status_code == 404
    assert db_client.put(
        f"/api/documents/{missing_id}/tags",
        json={"tags": ["database"]},
    ).status_code == 404
