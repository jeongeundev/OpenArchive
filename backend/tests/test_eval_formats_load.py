"""#167 형식별 평가 자료 적재기를 고정한다.

평가 수치는 "어떤 파일을 어떤 경로로 넣었는가"에 묶인다. 해시가 다른 파일로 재면 같은
평가셋 이름으로 다른 것을 재게 되고, 원본 교체를 건너뛰면 현재/과거 질문이 아무것도 재지 않는다.
정책브리핑 첨부는 공공누리가 텍스트에 한해서라 저장소에 넣지 않고 내려받는다 — 받은 파일의
해시가 평가셋과 다르면 멈추고, 스캔처럼 가공한 파생 파일은 같은 원본에서 다시 만든다.
"""

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.eval_formats_load import (
    derive_image_pptx,
    derive_scan_jpg,
    ensure_sources,
    source_files,
    verify_sources,
)

EVALSET = ROOT / "scripts" / "eval" / "formats.json"
MIXED_PDF = ROOT / "backend" / "tests" / "fixtures" / "mixed_tax_pages.pdf"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_committed_formats_evalset_is_well_formed_without_network():
    """저장소에 없는 자료는 내려받기(fetch)나 파생(derive) 출처가 있어야 재현된다."""
    evalset = json.loads(EVALSET.read_text())
    for item in source_files(evalset):
        if "fetch" in item:
            assert item["fetch"]["file_id"] and len(item["sha256"]) == 64
        elif "derive" in item:
            assert item["derive"]["kind"] in {"scan_jpg", "image_pptx"}
            assert len(item["derive"]["from"]["sha256"]) == 64
        else:
            assert _sha((ROOT / item["path"]).read_bytes()) == item["sha256"]
    titles = {source["title"] for source in evalset["sources"]}
    for item in evalset["queries"]:
        assert item["query"].strip() and item["category"].strip()
        assert set(item["relevant"]) <= titles
        for evidence in [item.get("evidence", []), *item.get("evidence_alternatives", [])]:
            assert {fragment["source"] for fragment in evidence} <= titles
        assert "evidence" in item or "evidence_alternatives" not in item
        assert item["relevant"] or item.get("evidence") is None


def test_verify_sources_rejects_a_changed_file(tmp_path):
    (tmp_path / "a.md").write_text("원본")
    evalset = {"sources": [{"path": "a.md", "sha256": "0" * 64, "title": "a", "owner": "evaluator"}]}
    with pytest.raises(ValueError, match="a.md"):
        verify_sources(evalset, tmp_path)


def test_ensure_sources_downloads_only_missing_files_and_stops_on_a_changed_download(tmp_path):
    calls = []

    def download(file_id: str) -> bytes:
        calls.append(file_id)
        return {"1": b"first", "2": b"second"}[file_id]

    (tmp_path / "kept.xlsx").write_bytes(b"second")
    evalset = {"sources": [
        {"path": "got/a.hwpx", "sha256": _sha(b"first"), "fetch": {"news_id": "9", "file_id": "1"}},
        {"path": "kept.xlsx", "sha256": _sha(b"second"), "fetch": {"news_id": "9", "file_id": "2"}},
    ]}
    assert ensure_sources(evalset, tmp_path, download=download) == []
    assert calls == ["1"]
    assert (tmp_path / "got" / "a.hwpx").read_bytes() == b"first"

    (tmp_path / "got" / "a.hwpx").unlink()
    evalset["sources"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="got/a.hwpx"):
        ensure_sources(evalset, tmp_path, download=download)


def test_derived_scan_has_no_text_layer_and_image_slides_extract_nothing():
    """스캔 가공본은 OCR 경로로, 그림뿐인 슬라이드는 「추출 텍스트 없음」 경로로 가야 평가 사례가 된다."""
    from openarchive.services.parsing import extract_text, needs_ocr

    pdf = MIXED_PDF.read_bytes()
    scan = derive_scan_jpg(pdf, page=1)
    assert scan[:3] == b"\xff\xd8\xff"
    assert needs_ocr("jpg", scan)
    assert derive_scan_jpg(pdf, page=1) == scan
    slides = derive_image_pptx(pdf, page=1)
    assert extract_text(slides, "pptx") == ""
    time.sleep(1.1)  # 작성 시각이 바이트에 들어가면 초 단위로 달라진다
    assert derive_image_pptx(pdf, page=1) == slides


def test_ensure_sources_derives_from_the_downloaded_original_and_reports_drift(tmp_path):
    """파생 파일 해시는 라이브러리 판에 따라 달라질 수 있다 — 막지 않고 경고로 남긴다."""
    pdf = MIXED_PDF.read_bytes()
    evalset = {"sources": [{
        "path": "scan.jpg", "sha256": "0" * 64,
        "derive": {"kind": "scan_jpg", "page": 1,
                   "from": {"path": "press.pdf", "fetch": {"news_id": "9", "file_id": "7"}, "sha256": _sha(pdf)}},
    }]}
    warnings = ensure_sources(evalset, tmp_path, download=lambda file_id: pdf)
    assert (tmp_path / "scan.jpg").read_bytes() == derive_scan_jpg(pdf, page=1)
    assert len(warnings) == 1 and "scan.jpg" in warnings[0]


async def test_load_creates_private_tagged_documents_replaces_and_records_rejections(migrated_db, tmp_path):
    import psycopg
    from scripts.eval_formats_load import load

    def source(name, content, **extra):
        data = content if isinstance(content, bytes) else content.encode()
        (tmp_path / name).write_bytes(data)
        return {"path": name, "sha256": _sha(data), **extra}

    evalset = {
        "tag": "eval-test",
        "users": {"evaluator": "alice", "other": "bob"},
        "sources": [
            source("v1.md", "9월 계수 1.21392", title="계수", owner="evaluator",
                   replaced_by=source("v2.md", "10월 계수 1.21169")),
            source("secret.md", "국내총책 A씨", title="비밀", owner="other"),
            # 그림뿐인 슬라이드는 #177부터 OCR 대상이라 거부되지 않는다 — 추출 텍스트가 빈 문서로 거부를 본다
            source("empty.md", " \n", title="빈 문서", owner="evaluator"),
        ],
    }
    outcomes = await load(evalset, tmp_path, migrated_db)
    assert [item["status"] for item in outcomes] == ["replaced", "created", "rejected"]
    assert outcomes[2]["error"]
    # 내려받는 원본이 넣은 파일과 같아야 원본 보관 경로를 거친 평가다 — 교체 문서는 새 판
    assert outcomes[0]["original_sha256"] == evalset["sources"][0]["replaced_by"]["sha256"]
    assert outcomes[1]["original_sha256"] == evalset["sources"][1]["sha256"]
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        rows = await (await conn.execute(
            "SELECT title, owner_id, visibility, tags, content, version FROM documents ORDER BY title"
        )).fetchall()
    assert rows == [
        ("계수", "alice", "private", ["eval-test"], "10월 계수 1.21169", 2),
        ("비밀", "bob", "private", ["eval-test"], "국내총책 A씨", 1),
    ]
    with pytest.raises(RuntimeError, match="이미"):
        await load(evalset, tmp_path, migrated_db)


def _one_replaced_source(tmp_path):
    (tmp_path / "v1.md").write_text("9월 계수")
    (tmp_path / "v2.md").write_text("10월 계수")
    return {
        "tag": "eval-test", "users": {"evaluator": "alice"},
        "sources": [{"path": "v1.md", "sha256": _sha("9월 계수".encode()), "title": "계수", "owner": "evaluator",
                     "replaced_by": {"path": "v2.md", "sha256": _sha("10월 계수".encode())}}],
    }


async def test_load_stops_when_the_stored_original_differs_from_the_input(migrated_db, tmp_path, monkeypatch):
    from scripts import eval_formats_load

    async def corrupted(conn, document_id, **kwargs):
        return {"sha256": "0" * 64}

    monkeypatch.setattr(eval_formats_load, "get_original_file", corrupted)
    with pytest.raises(RuntimeError, match="보관된 원본"):
        await eval_formats_load.load(_one_replaced_source(tmp_path), tmp_path, migrated_db)


async def test_load_stops_when_replacement_does_not_make_the_next_text_version(migrated_db, tmp_path, monkeypatch):
    """현재/과거 질문은 교체가 만든 텍스트 v2를 잰다 — 판이 안 쌓이면 과거 질문이 아무것도 재지 않는다."""
    from scripts import eval_formats_load

    real = eval_formats_load.replace_original_file

    async def same_version(conn, document_id, **kwargs):
        document = await real(conn, document_id, **kwargs)
        return {**document, "version": kwargs["client_version"]}

    monkeypatch.setattr(eval_formats_load, "replace_original_file", same_version)
    with pytest.raises(RuntimeError, match="텍스트 버전"):
        await eval_formats_load.load(_one_replaced_source(tmp_path), tmp_path, migrated_db)
