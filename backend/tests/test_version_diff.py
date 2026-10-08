import threading
from uuid import UUID

import psycopg
import pytest
from conftest import login_as, upload_document
from test_share_access import issue_share_token
from test_token_access import bearer, issue_token

from openarchive.services import documents


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
    """difflib 계산은 반복 줄이 많으면 수십 초라 이벤트 루프 밖 스레드에서 돈다."""
    doc = versions(db_client)
    loop_thread = threading.get_ident()
    threads = []
    diff_lines = documents._diff_lines

    def recording(old, new):
        threads.append(threading.get_ident())
        return diff_lines(old, new)

    monkeypatch.setattr(documents, "_diff_lines", recording)
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        result = await documents.diff_versions(conn, UUID(doc), user_id="alice", base=1, target=2)
    assert len(threads) == 1 and threads[0] != loop_thread
    assert result["hunks"][0]["lines"][1] == {"op": "removed", "text": "나"}


def test_line_pairs_counts_matching_line_pairs():
    # 「a」 2×1 + 「b」 1×2 — difflib이 맞춰 볼 같은 줄의 짝 수다.
    assert documents._line_pairs("a\na\nb", "a\nb\nb") == 4
    assert documents._line_pairs("a", "b") == 0


# 빈 줄 3000개 — 같은 줄의 짝이 3001² ≈ 900만으로 상한(500만)을 넘는다.
REPEATED = "\n" * 3000


def test_large_diff_is_not_computed(db_client, monkeypatch):
    """같은 줄의 짝이 상한을 넘으면 계산하지 않고 too_large로 알린다."""
    def must_not_run(old, new):
        raise AssertionError("상한을 넘는 비교를 계산했다")

    monkeypatch.setattr(documents, "_diff_lines", must_not_run)
    response = compare(db_client, versions(db_client, REPEATED + "끝 1", REPEATED + "끝 2"))
    assert response.status_code == 200
    assert response.json() == {
        "base": 1, "target": 2, "identical": False, "too_large": True, "hunks": [],
    }


def test_large_identical_versions_are_still_identical(db_client):
    """같은 내용 판정은 문자열 비교라 상한과 무관하다."""
    doc = versions(db_client, REPEATED + "끝 1", REPEATED + "끝 2")
    response = db_client.post(f"/api/documents/{doc}/versions/1/restore", json={"current_version": 2})
    assert response.status_code == 200
    response = compare(db_client, doc, 1, 3)
    assert response.json()["identical"] is True
    assert response.json()["too_large"] is False


@pytest.mark.parametrize(("limit", "too_large"), [(4, False), (3, True)])
def test_limit_boundary(db_client, monkeypatch, limit, too_large):
    """짝 수가 상한과 같으면 계산하고, 넘을 때만 too_large다."""
    old, new = "가\n가\n다", "가\n다\n다"
    assert documents._line_pairs(old, new) == 4
    monkeypatch.setattr(documents, "MAX_DIFF_LINE_PAIRS", limit)
    response = compare(db_client, versions(db_client, old, new))
    assert response.json()["too_large"] is too_large
    assert (response.json()["hunks"] == []) is too_large
