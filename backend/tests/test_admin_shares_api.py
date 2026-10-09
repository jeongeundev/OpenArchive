"""관리자 공유 조회와 비상 폐기의 세션 경계."""
import pytest
from conftest import login_as
from test_groups_api import issue_admin_token, login_admin
from test_shares_api import MISSING_ID, create_share, insert_document
from test_token_access import bearer


def seed_shares(client, dsn):
    shares = []
    for owner in ("kim", "lee"):
        login_as(client, owner)
        share = create_share(client, owner + " 공유")
        document = insert_document(dsn, title=owner + " 비밀 제목", owner=owner, visibility="private")
        assert client.put(f"/api/shares/{share['id']}/documents/{document}").status_code == 204
        response = client.post(f"/api/shares/{share['id']}/tokens", json={"name": "외부"})
        assert response.status_code == 201
        shares.append((share, response.json(), document))
    return shares


def test_admin_metadata_without_documents(db_client, migrated_db):
    shares = seed_shares(db_client, migrated_db)
    login_admin(db_client, migrated_db)
    response = db_client.get("/api/admin/shares")
    assert response.status_code == 200
    rows = response.json()
    assert {row["owner"] for row in rows} == {"kim", "lee"}
    for row in rows:
        assert set(row) == {"id", "name", "owner", "created_at", "document_count", "tokens"}
        assert row["name"] == row["owner"] + " 공유"
        assert row["document_count"] == 1
        assert set(row["tokens"][0]) == {
            "id", "name", "created_at", "expires_at", "last_used_at", "expired"
        }
    for _, token, document in shares:
        assert document not in response.text
        assert token["token"] not in response.text
    assert "비밀 제목" not in response.text


@pytest.mark.parametrize("identity,expected", [("anonymous", 401), ("regular", 403), ("token", 403)])
def test_admin_shares_session_boundary(db_client, migrated_db, identity, expected):
    headers = {}
    if identity == "regular":
        login_as(db_client, "kim")
    elif identity == "token":
        login_admin(db_client, migrated_db)
        headers = bearer(issue_admin_token(migrated_db))
        db_client.cookies.clear()
    assert db_client.get("/api/admin/shares", headers=headers).status_code == expected
    assert db_client.delete(
        f"/api/admin/shares/{MISSING_ID}/tokens/{MISSING_ID}", headers=headers
    ).status_code == expected


def test_emergency_revocation_and_actor(db_client, migrated_db):
    shares = seed_shares(db_client, migrated_db)
    share, token, _ = shares[0]
    db_client.cookies.clear()
    assert db_client.get("/api/documents", headers=bearer(token["token"])).status_code == 200
    login_admin(db_client, migrated_db)
    assert db_client.delete(
        f"/api/admin/shares/{share['id']}/tokens/{token['id']}"
    ).status_code == 204
    row = db_client.get("/api/admin/audit?action=share_changed").json()["items"][0]
    assert row["actor"] == "boss"
    assert row["detail"]["change"] == "token_revoked"
    assert row["detail"]["owner"] == "kim"
    assert row["detail"]["share_name"] == share["name"]
    db_client.cookies.clear()
    assert db_client.get("/api/documents", headers=bearer(token["token"])).status_code == 401


def test_wrong_share_missing_token_and_regular_denial(db_client, migrated_db):
    shares = seed_shares(db_client, migrated_db)
    share, token, _ = shares[0]
    login_as(db_client, "lee")
    assert db_client.delete(
        f"/api/admin/shares/{share['id']}/tokens/{token['id']}"
    ).status_code == 403
    login_admin(db_client, migrated_db)
    for share_id, token_id in ((share["id"], MISSING_ID), (shares[1][0]["id"], token["id"])):
        response = db_client.delete(f"/api/admin/shares/{share_id}/tokens/{token_id}")
        assert response.status_code == 404
        assert response.json()["detail"] == "토큰을 찾을 수 없습니다."
    db_client.cookies.clear()
    assert db_client.get("/api/documents", headers=bearer(token["token"])).status_code == 200
