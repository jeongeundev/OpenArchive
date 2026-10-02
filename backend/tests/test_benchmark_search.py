"""검색 벤치마크는 격리 스키마만 사용하고 실제 DB에서 정리된다."""

import importlib.util
from pathlib import Path

import psycopg
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/benchmark_search.py"


def load_benchmark():
    spec = importlib.util.spec_from_file_location("benchmark_search", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_rolls_back_fixture_without_changing_public_tables(migrated_db):
    benchmark = load_benchmark()
    with psycopg.connect(migrated_db) as conn:
        before = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
        conn.rollback()
        report = benchmark.run_benchmark(conn, documents=12, chunks_per_document=5, queries=2)
        assert report["fixture"]["chunks"] == 60
        assert report["fixture"]["distinct_vectors"] == 60
        assert len(report["queries"]) == 6
        assert all(row["private_leaks"] == 0 for row in report["queries"])
        assert all(row["exact_documents"] > 0 for row in report["queries"])
        assert report["cleaned_up"]
        assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == before
        assert (
            conn.execute(
                "SELECT count(*) FROM pg_namespace WHERE nspname=%s", (report["fixture"]["schema"],)
            ).fetchone()[0]
            == 0
        )


def test_failed_benchmark_also_rolls_back_fixture(migrated_db, monkeypatch):
    benchmark = load_benchmark()

    def fail(*args, **kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(benchmark, "measure_query", fail)
    with psycopg.connect(migrated_db) as conn:
        before = conn.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'oa_bench_%%'"
        ).fetchone()[0]
        conn.rollback()
        with pytest.raises(RuntimeError, match="fixture failure"):
            benchmark.run_benchmark(conn, documents=12, chunks_per_document=5, queries=1)
        assert (
            conn.execute(
                "SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'oa_bench_%%'"
            ).fetchone()[0]
            == before
        )


def test_hidden_only_fixture_reports_no_matches(migrated_db):
    benchmark = load_benchmark()
    with psycopg.connect(migrated_db) as conn:
        report = benchmark.run_benchmark(conn, documents=1, chunks_per_document=2, queries=1)
        assert all(row["direct_documents"] == 0 for row in report["queries"])
        assert report["summary"]["mean_document_overlap"] is None
        assert report["cleaned_up"]
