"""부하 측정의 실패 집계·유입 예약·파일 크기 검증."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/benchmark_pipeline.py"


def load_benchmark():
    spec = importlib.util.spec_from_file_location("benchmark_pipeline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_request_summary_counts_timeouts_and_http_failures():
    report = load_benchmark().request_summary(
        [
            {"status": 201, "ms": 10},
            {"status": 503, "ms": 20},
            {"error": "ReadTimeout", "ms": 30},
        ]
    )
    assert report["requests"] == 3
    assert report["failures"] == 2
    assert report["median_ms"] == 20


def test_large_file_contains_project_text_and_has_exact_requested_size():
    data = load_benchmark().large_file("OpenArchive 문서 검색", 2_000_000)
    assert len(data) == 2_000_000
    assert "OpenArchive 문서 검색" in data.decode("utf-8")
    assert "benchmark section 1" in data.decode("utf-8")


@pytest.mark.parametrize("duration,rate,expected", [(30, 4, 120), (1, 2, 2)])
def test_upload_schedule_has_fixed_arrival_rate(duration, rate, expected):
    offsets = load_benchmark().upload_schedule(duration, rate)
    assert len(offsets) == expected
    assert offsets[-1] < duration
    assert offsets[1] - offsets[0] == 1 / rate


def test_successful_but_empty_search_is_counted_separately():
    report = load_benchmark().request_summary(
        [
            {"status": 200, "ms": 10, "empty_results": True},
            {"status": 200, "ms": 20, "empty_results": False},
        ]
    )
    assert report["failures"] == 0
    assert report["empty_results"] == 1


def test_large_docx_keeps_extractable_project_text_below_text_limit():
    from io import BytesIO

    from docx import Document

    from openarchive.services.parsing import extract_text

    data = load_benchmark().large_docx("OpenArchive 프로젝트 아키텍처", 2_000_000)
    assert len(data) >= 1_900_000
    assert len(data) < 2_200_000
    document = Document(BytesIO(data))
    assert len(document.inline_shapes) == 1
    text = extract_text(data, "docx")
    assert "OpenArchive 프로젝트 아키텍처" in text
    assert len(text) < 500_000


def test_cli_rejects_zero_workers_before_creating_resources(tmp_path):
    import subprocess
    import sys

    output = tmp_path / "load"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--workers", "0", "--out", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "개수는 양의 정수여야 합니다" in result.stderr
    assert not output.exists()
