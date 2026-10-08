import threading
from uuid import UUID

import psycopg
import pytest
from conftest import login_as, upload_document
from test_share_access import issue_share_token
from test_token_access import bearer, issue_token

from openarchive.services import documents, textdiff


def versions(client, old="가\n나\n다", new="가\n나2\n다\n라"):
    response = upload_document(client, content=old.encode(), data={"visibility": "private"})
    assert response.status_code == 201
    doc = response.json()["id"]
    response = client.put(f"/api/documents/{doc}", json={"content": new, "version": 1})
    assert response.status_code == 200
    return doc


def compare(client, doc, base=1, target=2):
    return client.get(f"/api/documents/{doc}/versions/{base}/diff/{target}")


def test_changed_lines(db_client):
    response = compare(db_client, versions(db_client))
    assert response.status_code == 200
    assert response.json() == {"base": 1, "target": 2, "identical": False, "too_large": False, "hunks": [{"lines": [
        {"op": "equal", "text": "가"}, {"op": "removed", "text": "나"},
        {"op": "added", "text": "나2"}, {"op": "equal", "text": "다"},
        {"op": "added", "text": "라"},
    ]}]}
    assert all("\n" not in line["text"] for h in response.json()["hunks"] for line in h["lines"])


def test_identical_versions(db_client):
    doc = versions(db_client)
    response = db_client.post(f"/api/documents/{doc}/versions/1/restore", json={"current_version": 2})
    assert response.status_code == 200
    assert response.json()["version"] == 3
    for base, target in [(1, 3), (3, 1), (2, 2)]:
        response = compare(db_client, doc, base, target)
        assert response.status_code == 200
        assert response.json() == {
            "base": base, "target": target, "identical": True, "too_large": False, "hunks": [],
        }


@pytest.mark.parametrize("positions", [(14,), (4, 24)])
def test_context(db_client, positions):
    old = [f"줄 {i}" for i in range(1, 31)]
    new = old.copy()
    for pos in positions:
        new[pos] = "바뀜"
    response = compare(db_client, versions(db_client, "\n".join(old), "\n".join(new)))
    assert response.status_code == 200
    hunks = response.json()["hunks"]
    assert len(hunks) == len(positions)
    for hunk, pos in zip(hunks, positions, strict=True):
        assert hunk["lines"] == (
            [{"op": "equal", "text": s} for s in old[pos-3:pos]]
            + [{"op": "removed", "text": old[pos]}, {"op": "added", "text": "바뀜"}]
            + [{"op": "equal", "text": s} for s in old[pos+1:pos+4]]
        )
    assert all(line["text"] != "줄 1" for h in hunks for line in h["lines"])


def test_terminal_newline(db_client):
    response = compare(db_client, versions(db_client, "가\n", "가"))
    assert response.status_code == 200
    assert response.json()["identical"] is False


def test_visibility_and_missing(db_client):
    doc = versions(db_client)
    for base, target in [(1, 99), (99, 1)]:
        response = compare(db_client, doc, base, target)
        assert response.status_code == 404
        assert response.json() == {"detail": "문서를 찾을 수 없습니다."}
    for base, target in [(0, 1), (1, 0)]:
        assert compare(db_client, doc, base, target).status_code == 422
    login_as(db_client, "bob")
    response = compare(db_client, doc)
    assert response.status_code == 404
    assert response.json() == {"detail": "문서를 찾을 수 없습니다."}


def test_tokens(db_client):
    doc = versions(db_client)
    share = db_client.post("/api/shares", json={"name": "비교 공유"}).json()["id"]
    assert db_client.put(f"/api/shares/{share}/documents/{doc}").status_code == 204
    share_token = issue_share_token(db_client, share)["token"]
    read_token = issue_token(db_client, "alice", scope="read")["token"]
    db_client.cookies.clear()
    url = f"/api/documents/{doc}/versions/1/diff/2"
    response = db_client.get(url, headers=bearer(share_token))
    assert response.status_code == 403
    assert response.json() == {"detail": "공유 토큰으로는 열 수 없는 경로입니다."}
    response = db_client.get(url, headers=bearer(read_token))
    assert response.status_code == 200
    assert response.json()["identical"] is False


@pytest.mark.asyncio
async def test_service(db_client, migrated_db):
    doc = versions(db_client)
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        result = await documents.diff_versions(conn, UUID(doc), user_id="alice", base=1, target=2)
    assert result == compare(db_client, doc).json()
    assert result["hunks"][0]["lines"][1] == {"op": "removed", "text": "나"}


@pytest.mark.asyncio
async def test_diff_runs_off_event_loop(db_client, migrated_db, monkeypatch):
    """비교 계산은 예산 안에서도 1초 가까이 걸릴 수 있어 이벤트 루프 밖 스레드에서 돈다."""
    doc = versions(db_client)
    loop_thread = threading.get_ident()
    threads = []
    diff_hunks = textdiff.diff_hunks

    def recording(old, new):
        threads.append(threading.get_ident())
        return diff_hunks(old, new)

    monkeypatch.setattr(textdiff, "diff_hunks", recording)
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        result = await documents.diff_versions(conn, UUID(doc), user_id="alice", base=1, target=2)
    assert len(threads) == 1 and threads[0] != loop_thread
    assert result["hunks"][0]["lines"][1] == {"op": "removed", "text": "나"}


# 모든 줄이 바뀐 3000줄 — 편집량이 작업 예산을 넘는다.
OLD_LARGE = "\n".join(f"이전 {i}" for i in range(3000))
NEW_LARGE = "\n".join(f"새 {i}" for i in range(3000))


def test_large_diff_is_too_large(db_client):
    """편집량이 예산을 넘으면 비교 결과 대신 too_large로 알린다."""
    response = compare(db_client, versions(db_client, OLD_LARGE, NEW_LARGE))
    assert response.status_code == 200
    assert response.json() == {
        "base": 1, "target": 2, "identical": False, "too_large": True, "hunks": [],
    }


def test_large_identical_versions_are_still_identical(db_client):
    """같은 내용 판정은 문자열 비교라 예산과 무관하다."""
    doc = versions(db_client, OLD_LARGE, NEW_LARGE)
    response = db_client.post(f"/api/documents/{doc}/versions/1/restore", json={"current_version": 2})
    assert response.status_code == 200
    response = compare(db_client, doc, 1, 3)
    assert response.json()["identical"] is True
    assert response.json()["too_large"] is False


def test_terminal_newline_only_is_neither_identical_nor_too_large(db_client):
    response = compare(db_client, versions(db_client, "가\n", "가"))
    assert response.json() == {
        "base": 1, "target": 2, "identical": False, "too_large": False, "hunks": [],
    }
