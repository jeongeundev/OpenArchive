import hashlib
import io
import zipfile
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import psycopg
import pytest
from conftest import login_as, run_embedding_worker
from conftest import upload_document as upload
from docx import Document
from fastapi.testclient import TestClient
from test_parsing import hwp_without_text, hwpx_without_text

from app.config import get_settings


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

    # 대칭 비교만으로는 두 경로가 나란히 아무것도 만들지 않아도 통과한다.
    # 업로드 쪽 파생이 실제로 존재하는 것을 먼저 못박아 비교의 기준점을 세운다.
    jobs, versions, chunks, links, has_edges = derivatives[0]
    # 잡 2건 = 임베딩 잡 + ready 전이가 만든 관계 잡 (017)
    assert (jobs, versions, links, has_edges) == (2, 1, ["Target"], True)
    assert chunks > 0
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
        "detail": "문서에서 텍스트를 추출하지 못했습니다."
    }
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (0,)


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [("공문.hwp", "application/x-hwp"), ("공문.hwpx", "application/hwp+zip")],
)
def test_upload_hangul_document_extracts_text_and_keeps_the_original(
    db_client: TestClient, filename: str, media_type: str
):
    fixture = Path(__file__).parent / "fixtures" / f"committee_result.{filename.rsplit('.', 1)[1]}"
    original = fixture.read_bytes()

    created = upload(db_client, filename=filename, content=original)

    assert created.status_code == 201
    assert created.json()["content_type"] == filename.rsplit(".", 1)[1]
    detail = db_client.get(f"/api/documents/{created.json()['id']}").json()
    assert "2026년 제38차 위원회 결과" in detail["content"]
    download = db_client.get(f"/api/documents/{created.json()['id']}/file")
    assert download.content == original
    assert download.headers["content-type"] == media_type


@pytest.mark.parametrize(
    ("fixture_name", "expected_text", "media_type"),
    [
        (
            "office_budget.xlsx",
            "합계\t1500",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            "office_briefing.pptx",
            "둘째 슬라이드 노트",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ],
)
def test_upload_office_document_extracts_text_and_keeps_the_original(
    db_client: TestClient, fixture_name: str, expected_text: str, media_type: str
):
    original = (Path(__file__).parent / "fixtures" / fixture_name).read_bytes()

    created = upload(db_client, filename=fixture_name, content=original)

    assert created.status_code == 201
    assert created.json()["content_type"] == fixture_name.rsplit(".", 1)[1]
    detail = db_client.get(f"/api/documents/{created.json()['id']}").json()
    assert expected_text in detail["content"]
    download = db_client.get(f"/api/documents/{created.json()['id']}/file")
    assert download.content == original
    assert download.headers["content-type"] == media_type


@pytest.mark.parametrize("content_type", ["hwp", "hwpx"])
def test_upload_rejects_hangul_document_without_text(
    db_client: TestClient, migrated_db: str, tmp_path: Path, content_type: str
):
    content = (
        hwp_without_text(tmp_path / "empty.hwp") if content_type == "hwp" else hwpx_without_text()
    )

    response = upload(db_client, filename=f"빈 공문.{content_type}", content=content)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "문서에서 텍스트를 추출하지 못했습니다."
    }
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (0,)




def test_upload_rejects_unsupported_extension(db_client: TestClient):
    response = upload(db_client, filename="document.rtf")

    assert response.status_code == 400
    assert "pdf, docx, txt, md, hwp, hwpx, xlsx, pptx, png, jpg, jpeg" in response.json()["detail"]


def test_upload_rejects_non_utf8_text(db_client: TestClient):
    response = upload(db_client, content="한글".encode("cp949"))

    assert response.status_code == 400
    assert response.json()["detail"] == "텍스트 파일은 UTF-8 인코딩이어야 합니다."


def limit_upload_to_one_mb(monkeypatch) -> int:
    """업로드 상한을 1MB로 낮추고 그 바이트 수를 반환한다.

    기본값(50MB)으로 경계를 재면 테스트가 수십 MB를 만들어야 한다. 상한이 설정값에서
    온다는 사실 자체가 이 테스트들의 전제이므로, 작은 값으로 같은 경계를 검사한다.
    """
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    return 1_000_000


def test_oversized_upload_is_rejected_before_read(db_client: TestClient, monkeypatch):
    limit = limit_upload_to_one_mb(monkeypatch)

    async def fail_if_read(*args, **kwargs):
        raise AssertionError("oversized upload must be rejected before read()")

    monkeypatch.setattr("starlette.datastructures.UploadFile.read", fail_if_read)

    response = upload(db_client, content=b"x" * (limit + 1))

    assert response.status_code == 413


def test_upload_larger_than_its_declared_size_is_rejected(
    db_client: TestClient, monkeypatch
):
    """`file.size`는 클라이언트가 보내는 선언값이라 없거나 실제와 다를 수 있다.

    선언값만 보고 통과시키면 크기를 안 보내거나 줄여 보내는 클라이언트에게는 상한이
    사실상 없다. 위 검사는 큰 파일을 읽지 않기 위한 것이고, 경계 자체는 읽어들인
    바이트로도 지켜져야 한다.
    """
    limit = limit_upload_to_one_mb(monkeypatch)
    oversized = b"x" * (limit + 1)

    async def read_oversized(self, size: int = -1) -> bytes:
        return oversized

    monkeypatch.setattr("starlette.datastructures.UploadFile.read", read_oversized)

    response = upload(db_client, content=b"small")

    assert response.status_code == 413


def test_upload_limit_comes_from_settings(db_client: TestClient, monkeypatch):
    limit = limit_upload_to_one_mb(monkeypatch)

    response = upload(db_client, content=b"x" * (limit + 1))

    assert response.status_code == 413
    assert response.json()["detail"] == "업로드 파일은 1MB를 넘을 수 없습니다."


def test_extracted_text_over_service_limit_is_rejected(db_client: TestClient):
    response = upload(db_client, content=b"x" * 500_001)

    assert response.status_code == 400
    assert "500KB" in response.json()["detail"]


def test_upload_near_file_limit_with_small_extracted_text_succeeds(
    db_client: TestClient, monkeypatch
):
    limit = limit_upload_to_one_mb(monkeypatch)
    buf = io.BytesIO()
    document = Document()
    document.add_paragraph("OpenSQL near-limit document")
    document.save(buf)
    with zipfile.ZipFile(buf, "a", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("word/media/padding.bin", b"x" * 900_000)

    response = upload(db_client, filename="near-limit.docx", content=buf.getvalue())

    assert len(buf.getvalue()) < limit
    assert response.status_code == 201


def test_upload_stores_the_original_as_file_version_one(
    db_client: TestClient, migrated_db: str
):
    data = b"OpenSQL original bytes"
    document_id = upload(db_client, filename="original.txt", content=data).json()["id"]

    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            """
            SELECT file_version, text_version, filename, uploaded_by, sha256, size, data
            FROM document_files WHERE document_id = %s
            """,
            (document_id,),
        ).fetchall()

    assert rows == [
        (
            1,
            1,
            "original.txt",
            "alice",
            hashlib.sha256(data).hexdigest(),
            len(data),
            data,
        )
    ]


def test_original_and_document_are_committed_together(
    db_client: TestClient, migrated_db: str
):
    """원본 INSERT가 실패하면 문서도, 트리거가 만든 파생 행도 남지 않아야 한다."""
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            """
            CREATE FUNCTION reject_original() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION 'original insert rejected';
            END; $$
            """
        )
        conn.execute(
            """
            CREATE TRIGGER reject_original BEFORE INSERT ON document_files
              FOR EACH ROW EXECUTE FUNCTION reject_original()
            """
        )
    login_as(db_client, "alice")

    with pytest.raises(psycopg.errors.RaiseException, match="original insert rejected"):
        upload(db_client, filename="atomic.txt", content=b"OpenSQL atomic")

    with psycopg.connect(migrated_db) as conn:
        for table in ("documents", "document_versions", "embedding_jobs", "document_files"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,), table


def test_text_ingest_has_no_original_file(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    response = db_client.post(
        "/api/documents/text",
        json={"title": "직접 공급", "content": "OpenSQL text only"},
    )
    assert response.status_code == 201

    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT count(*) FROM document_files WHERE document_id = %s",
            (response.json()["id"],),
        ).fetchone() == (0,)


def test_upload_requires_user_id(db_client: TestClient):
    response = upload(db_client, user_id=None)

    assert response.status_code == 401


def test_read_token_can_read_but_cannot_use_any_document_write_endpoint(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client).json()["id"]
    token = "alice-read-token"
    with psycopg.connect(migrated_db) as conn:
        user_id = conn.execute(
            "SELECT id FROM users WHERE username = 'alice'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, scope) VALUES (%s, 'read-test', %s, 'read')",
            (user_id, hashlib.sha256(token.encode()).hexdigest()),
        )

    db_client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    assert db_client.get("/api/documents", headers=headers).status_code == 200

    responses = [
        db_client.post(
            "/api/documents",
            headers=headers,
            files={"file": ("blocked.txt", b"blocked", "text/plain")},
        ),
        db_client.post(
            "/api/documents/text",
            headers=headers,
            json={"title": "Blocked", "content": "blocked"},
        ),
        db_client.put(
            f"/api/documents/{document_id}",
            headers=headers,
            json={"content": "blocked", "version": 1},
        ),
        db_client.put(
            f"/api/documents/{document_id}/tags",
            headers=headers,
            json={"tags": ["blocked"]},
        ),
        db_client.delete(f"/api/documents/{document_id}", headers=headers),
        db_client.post(f"/api/documents/{document_id}/reembed", headers=headers),
        db_client.post(
            f"/api/documents/{document_id}/reextract",
            headers=headers,
            json={"current_version": 1},
        ),
    ]

    assert [response.status_code for response in responses] == [403] * 7


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
        # 임베딩 잡만 센다 — 위 셋업의 ready 전이가 만든 관계 잡은 태그 교체와 무관하다.
        assert conn.execute(
            "SELECT count(*) FROM embedding_jobs"
            " WHERE document_id = %s AND kind = 'embed' AND status = 'pending'",
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
        # 임베딩 잡만 센다 — 위 셋업의 ready 전이가 만든 관계 잡은 편집과 무관하다.
        assert conn.execute(
            "SELECT count(*) FROM embedding_jobs"
            " WHERE document_id = %s AND kind = 'embed' AND status = 'pending'",
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


def restore(
    client: TestClient,
    document_id: str,
    *,
    version: int,
    current_version: int,
    user_id="alice",
):
    if user_id is not None:
        login_as(client, user_id)
    return client.post(
        f"/api/documents/{document_id}/versions/{version}/restore",
        json={"current_version": current_version},
    )


def test_past_version_endpoint_returns_that_versions_text(db_client: TestClient):
    login_as(db_client, "alice")
    created = db_client.post(
        "/api/documents/text", json={"title": "정책", "content": "처음 내용"}
    ).json()
    edit(db_client, created["id"], content="고친 내용", version=1)

    response = db_client.get(f"/api/documents/{created['id']}/versions/1")

    assert response.status_code == 200
    assert response.json()["content"] == "처음 내용"
    assert response.json()["version"] == 1


def test_past_version_endpoint_hides_private_documents_from_others(
    db_client: TestClient,
):
    """열람 범위 밖 문서의 버전은 404다 — 403이면 문서의 존재를 알려준다 (ADR-027)."""
    login_as(db_client, "alice")
    created = db_client.post(
        "/api/documents/text",
        json={"title": "비공개", "content": "숨은 내용", "visibility": "private"},
    ).json()

    login_as(db_client, "bob")
    response = db_client.get(f"/api/documents/{created['id']}/versions/1")

    assert response.status_code == 404


def test_past_version_endpoint_rejects_a_version_that_never_existed(
    db_client: TestClient,
):
    login_as(db_client, "alice")
    created = db_client.post(
        "/api/documents/text", json={"title": "정책", "content": "처음 내용"}
    ).json()

    assert db_client.get(f"/api/documents/{created['id']}/versions/2").status_code == 404


def test_restore_endpoint_appends_a_new_version_and_requeues_embedding(
    db_client: TestClient, migrated_db: str
):
    """되돌리기는 새 버전을 만들고 파이프라인을 다시 태운다 (ADR-037 결정 2)."""
    login_as(db_client, "alice")
    created = db_client.post(
        "/api/documents/text", json={"title": "정책", "content": "처음 내용"}
    ).json()
    edit(db_client, created["id"], content="고친 내용", version=1)

    response = restore(db_client, created["id"], version=1, current_version=2)

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 3
    assert body["content"] == "처음 내용"
    assert body["embedding_status"] == "pending"
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT version FROM document_versions"
            " WHERE document_id = %s ORDER BY version",
            (created["id"],),
        ).fetchall() == [(1,), (2,), (3,)]


def test_restore_endpoint_rejects_a_stale_current_version(db_client: TestClient):
    login_as(db_client, "alice")
    created = db_client.post(
        "/api/documents/text", json={"title": "정책", "content": "처음 내용"}
    ).json()
    edit(db_client, created["id"], content="고친 내용", version=1)

    response = restore(db_client, created["id"], version=1, current_version=1)

    assert response.status_code == 409
    assert response.json()["current_version"] == 2


def test_restore_endpoint_refuses_a_non_owner_of_a_public_document(
    db_client: TestClient,
):
    login_as(db_client, "alice")
    created = db_client.post(
        "/api/documents/text", json={"title": "공개 정책", "content": "처음 내용"}
    ).json()
    edit(db_client, created["id"], content="고친 내용", version=1)

    response = restore(
        db_client, created["id"], version=1, current_version=2, user_id="bob"
    )

    assert response.status_code == 403


ORIGINAL_FILE_KEYS = {
    "file_version",
    "filename",
    "size",
    "sha256",
    "text_version",
    "uploaded_by",
    "uploaded_at",
}


def test_detail_lists_original_file_versions_without_bytes(db_client: TestClient):
    data = b"OpenSQL original"
    uploaded_id = upload(db_client, filename="original.txt", content=data).json()["id"]
    supplied_id = db_client.post(
        "/api/documents/text", json={"title": "직접 공급", "content": "text only"}
    ).json()["id"]

    files = db_client.get(f"/api/documents/{uploaded_id}").json()["files"]
    assert len(files) == 1
    assert set(files[0]) == ORIGINAL_FILE_KEYS
    assert files[0]["file_version"] == 1
    assert files[0]["filename"] == "original.txt"
    assert files[0]["size"] == len(data)
    assert files[0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert files[0]["text_version"] == 1
    assert files[0]["uploaded_by"] == "alice"

    assert db_client.get(f"/api/documents/{supplied_id}").json()["files"] == []


def test_download_returns_the_exact_uploaded_bytes(db_client: TestClient):
    """추출 텍스트가 아니라 올린 파일 바이트 그대로가 나온다 — DOCX는 둘이 전혀 다르다."""
    buffer = io.BytesIO()
    docx = Document()
    docx.add_paragraph("OpenSQL 원본 보관")
    docx.save(buffer)
    data = buffer.getvalue()
    document_id = upload(db_client, filename="report.docx", content=data).json()["id"]

    response = db_client.get(f"/api/documents/{document_id}/file")

    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == hashlib.sha256(data).hexdigest()
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_download_specific_file_version(db_client: TestClient):
    data = b"OpenSQL versioned original"
    document_id = upload(db_client, filename="v.md", content=data).json()["id"]

    response = db_client.get(f"/api/documents/{document_id}/files/1")
    assert response.status_code == 200
    assert response.content == data

    missing = db_client.get(f"/api/documents/{document_id}/files/2")
    assert missing.status_code == 404
    assert missing.json()["detail"] != "문서를 찾을 수 없습니다."


def test_download_of_another_users_private_document_is_404(db_client: TestClient):
    document_id = upload(
        db_client, filename="secret.txt", data={"visibility": "private"}
    ).json()["id"]

    login_as(db_client, "bob")
    not_found = db_client.get(f"/api/documents/{uuid4()}/file").json()["detail"]
    for path in ("file", "files/1"):
        response = db_client.get(f"/api/documents/{document_id}/{path}")
        assert response.status_code == 404
        assert response.json()["detail"] == not_found


def test_download_without_original_is_404(db_client: TestClient):
    login_as(db_client, "alice")
    document_id = db_client.post(
        "/api/documents/text", json={"title": "원본 없음", "content": "text only"}
    ).json()["id"]

    response = db_client.get(f"/api/documents/{document_id}/file")

    assert response.status_code == 404
    assert response.json()["detail"] == "원본 파일이 없습니다."


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("한글 문서.md", "text/markdown; charset=utf-8"),
        ("notes.txt", "text/plain; charset=utf-8"),
    ],
)
def test_download_headers_force_attachment(
    db_client: TestClient, filename: str, media_type: str
):
    document_id = upload(db_client, filename=filename, content=b"OpenSQL").json()["id"]

    response = db_client.get(f"/api/documents/{document_id}/file")

    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert f"filename*=UTF-8''{quote(filename)}" in disposition
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"] == media_type


def test_download_media_type_ignores_the_uploaded_content_type(db_client: TestClient):
    """업로더가 보낸 Content-Type(text/html)이 응답에 새지 않는다 — 확장자 고정 매핑만 쓴다."""
    login_as(db_client, "alice")
    document_id = db_client.post(
        "/api/documents",
        files={"file": ("page.txt", b"<script>alert(1)</script>", "text/html")},
    ).json()["id"]

    response = db_client.get(f"/api/documents/{document_id}/file")

    assert response.headers["content-type"] == "text/plain; charset=utf-8"


def test_read_token_can_download(db_client: TestClient, migrated_db: str):
    data = b"OpenSQL token download"
    document_id = upload(db_client, content=data).json()["id"]
    token = "alice-download-token"
    with psycopg.connect(migrated_db) as conn:
        user_id = conn.execute(
            "SELECT id FROM users WHERE username = 'alice'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, scope) VALUES (%s, 'read-dl', %s, 'read')",
            (user_id, hashlib.sha256(token.encode()).hexdigest()),
        )

    db_client.cookies.clear()
    response = db_client.get(
        f"/api/documents/{document_id}/file",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.content == data


def test_download_requires_login(db_client: TestClient):
    document_id = upload(db_client).json()["id"]
    db_client.post("/api/auth/logout")

    assert db_client.get(f"/api/documents/{document_id}/file").status_code == 401
    assert db_client.get(f"/api/documents/{document_id}/files/1").status_code == 401


# ── 원본 교체 — 새 판을 쌓고 이전 판은 지우지 않는다 (ADR-046) ──────────────


def replace_file(
    client: TestClient,
    document_id: str,
    *,
    filename: str = "guide.txt",
    content: bytes = b"OpenSQL guide v2",
    current_version: int = 1,
    user_id: str | None = "alice",
    headers: dict[str, str] | None = None,
):
    if user_id is not None:
        login_as(client, user_id)
    return client.put(
        f"/api/documents/{document_id}/file",
        files={"file": (filename, content, "application/octet-stream")},
        data={"current_version": str(current_version)},
        headers=headers,
    )


def docx_bytes(text: str, *, author: str) -> bytes:
    """본문은 같고 메타데이터만 다른 DOCX — 바이트는 다르지만 추출 텍스트는 같다."""
    buffer = io.BytesIO()
    docx = Document()
    docx.core_properties.author = author
    docx.add_paragraph(text)
    docx.save(buffer)
    return buffer.getvalue()


def file_rows(dsn: str, document_id: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            """
            SELECT file_version, filename, sha256, text_version, uploaded_by
            FROM document_files WHERE document_id = %s ORDER BY file_version
            """,
            (document_id,),
        ).fetchall()


def test_replace_adds_a_new_file_version_and_keeps_the_old_one(
    db_client: TestClient, migrated_db: str
):
    first = b"OpenSQL guide v1"
    document_id = upload(db_client, content=first).json()["id"]

    response = replace_file(db_client, document_id, content=b"OpenSQL guide v2")

    assert response.status_code == 200
    assert [row[0] for row in file_rows(migrated_db, document_id)] == [1, 2]
    old = db_client.get(f"/api/documents/{document_id}/files/1")
    assert old.status_code == 200
    assert old.content == first
    assert db_client.get(f"/api/documents/{document_id}/file").content == b"OpenSQL guide v2"


def test_replace_creates_a_new_text_version_via_trigger(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client).json()["id"]
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE embedding_jobs SET status = 'done' WHERE document_id = %s", (document_id,)
        )

    body = replace_file(db_client, document_id, content=b"OpenSQL replaced text").json()

    assert body["version"] == 2
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT version, content FROM document_versions WHERE document_id = %s AND version = 2",
            (document_id,),
        ).fetchone() == (2, "OpenSQL replaced text")
        assert conn.execute(
            """
            SELECT count(*) FROM embedding_jobs
            WHERE document_id = %s AND kind = 'embed' AND status = 'pending'
            """,
            (document_id,),
        ).fetchone() == (1,)
    assert file_rows(migrated_db, document_id)[1][3] == 2


def test_replace_updates_filename_and_content_type(db_client: TestClient):
    document_id = upload(
        db_client,
        filename="report.txt",
        data={"title": "분기 보고", "tags": ["재무"], "visibility": "private"},
    ).json()["id"]

    body = replace_file(
        db_client,
        document_id,
        filename="report.docx",
        content=docx_bytes("분기 보고 새 판", author="a"),
    ).json()

    assert body["id"] == document_id
    assert body["filename"] == "report.docx"
    assert body["content_type"] == "docx"
    assert body["title"] == "분기 보고"
    assert body["tags"] == ["재무"]
    assert body["visibility"] == "private"


def test_replace_with_identical_bytes_is_a_no_op(db_client: TestClient, migrated_db: str):
    data = b"OpenSQL same bytes"
    document_id = upload(db_client, content=data).json()["id"]

    response = replace_file(db_client, document_id, filename="renamed.txt", content=data)

    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert response.json()["filename"] == "guide.txt"
    assert len(file_rows(migrated_db, document_id)) == 1
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT count(*) FROM document_versions WHERE document_id = %s", (document_id,)
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM embedding_jobs WHERE document_id = %s", (document_id,)
        ).fetchone() == (1,)


def test_replace_with_same_extracted_text_adds_file_but_no_text_version(
    db_client: TestClient, migrated_db: str
):
    first = docx_bytes("같은 본문", author="first")
    second = docx_bytes("같은 본문", author="second")
    assert first != second
    document_id = upload(db_client, filename="same.docx", content=first).json()["id"]

    response = replace_file(
        db_client, document_id, filename="same-renamed.docx", content=second
    )

    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert response.json()["filename"] == "same-renamed.docx"
    rows = file_rows(migrated_db, document_id)
    assert [(row[0], row[1], row[3]) for row in rows] == [
        (1, "same.docx", 1),
        (2, "same-renamed.docx", 1),
    ]
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT count(*) FROM document_versions WHERE document_id = %s", (document_id,)
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM embedding_jobs WHERE document_id = %s", (document_id,)
        ).fetchone() == (1,)


def test_replace_with_stale_version_is_409(db_client: TestClient, migrated_db: str):
    document_id = upload(db_client).json()["id"]
    assert edit(db_client, document_id, content="edited", version=1).status_code == 200

    response = replace_file(db_client, document_id, current_version=1)

    assert response.status_code == 409
    assert response.json()["current_version"] == 2
    assert len(file_rows(migrated_db, document_id)) == 1
    assert db_client.get(f"/api/documents/{document_id}").json()["content"] == "edited"


def test_replace_by_non_owner_is_403_and_private_is_404(
    db_client: TestClient, migrated_db: str
):
    public_id = upload(db_client, filename="public.txt").json()["id"]
    private_id = upload(
        db_client, filename="private.txt", data={"visibility": "private"}
    ).json()["id"]

    assert replace_file(db_client, public_id, user_id="bob").status_code == 403
    assert replace_file(db_client, private_id, user_id="bob").status_code == 404
    assert len(file_rows(migrated_db, public_id)) == 1
    assert len(file_rows(migrated_db, private_id)) == 1


def test_replace_registers_first_original_for_documents_without_one(
    db_client: TestClient, migrated_db: str
):
    login_as(db_client, "alice")
    document_id = db_client.post(
        "/api/documents/text", json={"title": "직접 공급", "content": "text only"}
    ).json()["id"]

    response = replace_file(
        db_client, document_id, filename="now-a-file.md", content=b"# from a file"
    )

    assert response.status_code == 200
    assert response.json()["filename"] == "now-a-file.md"
    assert response.json()["version"] == 2
    assert [(row[0], row[1], row[3]) for row in file_rows(migrated_db, document_id)] == [
        (1, "now-a-file.md", 2)
    ]


def test_replace_rejects_oversized_file(
    db_client: TestClient, migrated_db: str, monkeypatch
):
    document_id = upload(db_client).json()["id"]
    limit = limit_upload_to_one_mb(monkeypatch)

    response = replace_file(db_client, document_id, content=b"x" * (limit + 1))

    assert response.status_code == 413
    assert len(file_rows(migrated_db, document_id)) == 1


def test_replace_rejects_unsupported_type_and_blank_text(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client).json()["id"]

    unsupported = replace_file(db_client, document_id, filename="document.rtf")
    assert unsupported.status_code == 400
    assert "pdf, docx, txt, md, hwp, hwpx, xlsx, pptx, png, jpg, jpeg" in unsupported.json()["detail"]

    blank = replace_file(db_client, document_id, content=b" \t\r\n\f")
    assert blank.status_code == 400
    assert blank.json() == {
        "detail": "문서에서 텍스트를 추출하지 못했습니다."
    }

    non_utf8 = replace_file(db_client, document_id, content="한글".encode("cp949"))
    assert non_utf8.status_code == 400
    assert non_utf8.json()["detail"] == "텍스트 파일은 UTF-8 인코딩이어야 합니다."

    assert len(file_rows(migrated_db, document_id)) == 1
    assert db_client.get(f"/api/documents/{document_id}").json()["version"] == 1


def test_replace_is_forbidden_for_read_token(db_client: TestClient, migrated_db: str):
    document_id = upload(db_client).json()["id"]
    token = "alice-read-replace-token"
    with psycopg.connect(migrated_db) as conn:
        user_id = conn.execute(
            "SELECT id FROM users WHERE username = 'alice'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, scope) VALUES (%s, 'read-rp', %s, 'read')",
            (user_id, hashlib.sha256(token.encode()).hexdigest()),
        )

    db_client.cookies.clear()
    response = replace_file(
        db_client,
        document_id,
        user_id=None,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert len(file_rows(migrated_db, document_id)) == 1


# ── 재추출 — 보관된 최신 원본에서 텍스트를 다시 뽑는다 ─────────────────────


def reextract(
    client: TestClient,
    document_id: str,
    *,
    current_version: int = 1,
    user_id: str | None = "alice",
):
    if user_id is not None:
        login_as(client, user_id)
    return client.post(
        f"/api/documents/{document_id}/reextract",
        json={"current_version": current_version},
    )


def text_version_count(dsn: str, document_id: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT count(*) FROM document_versions WHERE document_id = %s", (document_id,)
        ).fetchone()[0]


def pending_embed_jobs(dsn: str, document_id: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            """
            SELECT count(*) FROM embedding_jobs
            WHERE document_id = %s AND kind = 'embed' AND status = 'pending'
            """,
            (document_id,),
        ).fetchone()[0]


def finish_jobs(dsn: str, document_id: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE embedding_jobs SET status = 'done' WHERE document_id = %s", (document_id,)
        )


def test_reextract_creates_a_new_text_version_when_text_differs(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client, content=b"OpenSQL original text").json()["id"]
    assert edit(db_client, document_id, content="hand edited", version=1).status_code == 200
    finish_jobs(migrated_db, document_id)

    response = reextract(db_client, document_id, current_version=2)

    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert body["version"] == 3
    assert body["content"] == "OpenSQL original text"
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT content FROM document_versions WHERE document_id = %s AND version = 3",
            (document_id,),
        ).fetchone() == ("OpenSQL original text",)
    assert pending_embed_jobs(migrated_db, document_id) == 1
    # 원본은 그대로다 — 재추출은 판을 만들지 않는다.
    assert len(file_rows(migrated_db, document_id)) == 1


def test_reextract_without_changes_is_a_no_op(db_client: TestClient, migrated_db: str):
    document_id = upload(db_client).json()["id"]
    finish_jobs(migrated_db, document_id)

    response = reextract(db_client, document_id)

    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert response.json()["version"] == 1
    assert text_version_count(migrated_db, document_id) == 1
    assert pending_embed_jobs(migrated_db, document_id) == 0


def test_reextract_uses_the_latest_file_version(db_client: TestClient, migrated_db: str):
    document_id = upload(db_client, content=b"first file").json()["id"]
    assert replace_file(db_client, document_id, content=b"second file").status_code == 200
    assert edit(db_client, document_id, content="hand edited", version=2).status_code == 200

    response = reextract(db_client, document_id, current_version=3)

    assert response.status_code == 200
    assert response.json()["content"] == "second file"
    assert response.json()["version"] == 4


def test_reextract_applies_an_improved_parser(
    db_client: TestClient, migrated_db: str, monkeypatch
):
    from app.services import documents as service

    document_id = upload(db_client, content=b"OpenSQL guide").json()["id"]
    finish_jobs(migrated_db, document_id)
    monkeypatch.setattr(
        service, "extract_text", lambda data, content_type: "improved: " + data.decode()
    )

    response = reextract(db_client, document_id)

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["version"] == 2
    assert response.json()["content"] == "improved: OpenSQL guide"
    assert pending_embed_jobs(migrated_db, document_id) == 1


def test_reextract_without_original_is_409(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    text_id = db_client.post(
        "/api/documents/text", json={"title": "직접 공급", "content": "text only"}
    ).json()["id"]
    uploaded_id = upload(db_client).json()["id"]
    with psycopg.connect(migrated_db) as conn:
        # 이 기능 이전에 업로드되어 원본이 없는 문서를 흉내 낸다.
        conn.execute("DELETE FROM document_files WHERE document_id = %s", (uploaded_id,))

    for document_id in (text_id, uploaded_id):
        response = reextract(db_client, document_id)
        assert response.status_code == 409
        assert response.json() == {
            "detail": "원본 파일이 없는 문서는 다시 추출할 수 없습니다."
        }
        assert text_version_count(migrated_db, document_id) == 1


def test_reextract_with_stale_version_is_409(db_client: TestClient, migrated_db: str):
    document_id = upload(db_client).json()["id"]
    assert edit(db_client, document_id, content="edited", version=1).status_code == 200

    response = reextract(db_client, document_id, current_version=1)

    assert response.status_code == 409
    assert response.json()["current_version"] == 2
    assert db_client.get(f"/api/documents/{document_id}").json()["content"] == "edited"
    assert text_version_count(migrated_db, document_id) == 2


def test_reextract_by_non_owner_is_403_and_private_is_404(
    db_client: TestClient, migrated_db: str
):
    public_id = upload(db_client, filename="public.txt").json()["id"]
    private_id = upload(
        db_client, filename="private.txt", data={"visibility": "private"}
    ).json()["id"]
    for document_id in (public_id, private_id):
        assert edit(db_client, document_id, content="edited", version=1).status_code == 200

    assert reextract(db_client, public_id, current_version=2, user_id="bob").status_code == 403
    assert reextract(db_client, private_id, current_version=2, user_id="bob").status_code == 404
    assert text_version_count(migrated_db, public_id) == 2
    assert text_version_count(migrated_db, private_id) == 2


def test_reextract_rejects_unparseable_and_blank_originals(
    db_client: TestClient, migrated_db: str
):
    document_id = upload(db_client).json()["id"]
    assert edit(db_client, document_id, content="edited", version=1).status_code == 200

    def swap_original(data: bytes) -> None:
        with psycopg.connect(migrated_db) as conn:
            conn.execute(
                "UPDATE document_files SET data = %s WHERE document_id = %s",
                (data, document_id),
            )

    swap_original("한글".encode("cp949"))
    broken = reextract(db_client, document_id, current_version=2)
    assert broken.status_code == 400
    assert broken.json()["detail"] == "텍스트 파일은 UTF-8 인코딩이어야 합니다."

    swap_original(b" \t\r\n\f")
    blank = reextract(db_client, document_id, current_version=2)
    assert blank.status_code == 400
    assert blank.json() == {
        "detail": "문서에서 텍스트를 추출하지 못했습니다."
    }

    document = db_client.get(f"/api/documents/{document_id}").json()
    assert (document["version"], document["content"]) == (2, "edited")


def count_documents(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT count(*) FROM documents").fetchone()[0]


def test_upload_retried_with_the_same_idempotency_key_returns_the_first_document(
    db_client: TestClient, migrated_db: str
):
    """응답을 잃은 업로드를 같은 키로 다시 보내면 처음 문서가 같은 상태 코드로 온다 (ADR-047)."""
    login_as(db_client, "alice")

    def send():
        return db_client.post(
            "/api/documents",
            files={"file": ("guide.txt", b"OpenSQL guide", "text/plain")},
            headers={"Idempotency-Key": "upload-1"},
        )

    first, second = send(), send()

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json()["id"] == first.json()["id"]
    assert count_documents(migrated_db) == 1


def test_text_ingest_retried_with_the_same_idempotency_key_returns_the_first_document(
    db_client: TestClient, migrated_db: str
):
    login_as(db_client, "alice")
    body = {"title": "재시도", "content": "본문"}
    headers = {"Idempotency-Key": "text-1"}

    first = db_client.post("/api/documents/text", json=body, headers=headers)
    second = db_client.post("/api/documents/text", json=body, headers=headers)

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json()["id"] == first.json()["id"]
    assert count_documents(migrated_db) == 1


def test_reusing_an_idempotency_key_for_a_different_request_is_422(
    db_client: TestClient, migrated_db: str
):
    login_as(db_client, "alice")
    headers = {"Idempotency-Key": "text-1"}
    db_client.post("/api/documents/text", json={"title": "a", "content": "본문"}, headers=headers)

    response = db_client.post(
        "/api/documents/text", json={"title": "a", "content": "다른 본문"}, headers=headers
    )

    assert response.status_code == 422
    assert "Idempotency-Key" in response.json()["detail"]
    assert count_documents(migrated_db) == 1


@pytest.mark.parametrize("key", ["", "k" * 256])
def test_an_idempotency_key_outside_1_to_255_characters_is_rejected(
    db_client: TestClient, migrated_db: str, key: str
):
    login_as(db_client, "alice")

    response = db_client.post(
        "/api/documents/text",
        json={"title": "a", "content": "본문"},
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 422
    assert count_documents(migrated_db) == 0


@pytest.mark.parametrize("fixture_name", ["scan_tax_page1.jpg", "scan_tax_pages.pdf"])
def test_upload_scan_returns_a_pending_extraction_document(
    db_client: TestClient, migrated_db: str, fixture_name: str
):
    original = (Path(__file__).parent / "fixtures" / fixture_name).read_bytes()

    created = upload(db_client, filename=fixture_name, content=original)

    assert created.status_code == 201
    assert created.json()["extraction_status"] == "pending"
    document_id = created.json()["id"]
    detail = db_client.get(f"/api/documents/{document_id}").json()
    assert (detail["extraction_status"], detail["content"], detail["versions"]) == (
        "pending",
        "",
        [],
    )
    assert [f["text_version"] for f in detail["files"]] == [None]
    listed = {d["id"]: d for d in db_client.get("/api/documents").json()}
    assert listed[document_id]["extraction_status"] == "pending"
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT kind, status FROM embedding_jobs WHERE document_id = %s", (document_id,)
        ).fetchall() == [("extract", "pending")]


def test_upload_with_text_reports_done_extraction(db_client: TestClient):
    created = upload(db_client)

    assert created.json()["extraction_status"] == "done"
    detail = db_client.get(f"/api/documents/{created.json()['id']}").json()
    assert detail["extraction_status"] == "done"
