#!/usr/bin/env python3
"""제품 마이그레이션·검색 SQL로 HNSW 비용을 측정한다. 의미 검색 품질 평가는 아니다.

DATABASE_URL=... backend/.venv/bin/python scripts/benchmark_search.py --out results.json
기존 테이블은 건드리지 않으며 CREATE SCHEMA 권한과 설치된 vector·pg_trgm이 필요하다.
모든 fixture·인덱스·통계는 한 plain 트랜잭션 안에서 만들고 성공/실패 시 rollback한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import time
import uuid
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from openarchive.config import get_settings
from openarchive.migrations import migration_files
from openarchive.services.search import EF_SEARCH, SEARCH_SQL
from openarchive.vectors import to_pgvector_literal

# + 0은 후보 벡터 정렬의 HNSW 사용만 막는다. 문서·발췌의 B-tree 인덱스는 유지한다.
EXACT_SQL = SEARCH_SQL.replace(
    "ORDER BY c.embedding <=> %(qvec)s::vector",
    "ORDER BY (c.embedding <=> %(qvec)s::vector) + 0",
    1,
)


def uses_hnsw(node):
    return node.get("Index Name") == "idx_chunks_embedding" or any(
        uses_hnsw(child) for child in node.get("Plans", [])
    )


def measure_query(conn, params):
    observations = {}
    for label, query in [("hnsw", SEARCH_SQL), ("exact", EXACT_SQL)]:
        start = time.perf_counter()
        rows = conn.execute(query, params).fetchall()
        observations[label] = {"ms": (time.perf_counter() - start) * 1000, "rows": rows}
    plan = conn.execute(
        "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + SEARCH_SQL, params
    ).fetchone()[0][0]
    direct = {r[0] for r in observations["hnsw"]["rows"] if r[9] is None}
    exact = {r[0] for r in observations["exact"]["rows"] if r[9] is None}
    leaks = conn.execute(
        "SELECT count(*) FROM documents WHERE id=ANY(%s::uuid[]) "
        "AND visibility='private' AND owner_id<>%s",
        (list(direct), params["user"]),
    ).fetchone()[0]
    assert leaks == 0
    return {
        "hnsw_ms": observations["hnsw"]["ms"],
        "exact_ms": observations["exact"]["ms"],
        "direct_documents": len(direct),
        "exact_documents": len(exact),
        "document_overlap": len(direct & exact) / len(exact) if exact else None,
        "private_leaks": leaks,
        "hnsw_used": uses_hnsw(plan["Plan"]),
        "plan": plan,
    }


def run_benchmark(conn, *, documents=500, chunks_per_document=40, queries=5):
    """호출자가 연결을 관리한다. 검증 트랜잭션의 데이터는 반드시 되돌린다."""
    schema = "oa_bench_" + uuid.uuid4().hex
    rng = random.Random(20261003)
    doc_ids = [uuid.uuid4() for _ in range(documents)]
    vectors = []
    report = {
        "scope": "synthetic independent dense vectors; SQL/index benchmark only",
        "sql_sha256": hashlib.sha256(SEARCH_SQL.encode()).hexdigest(),
        "queries": [],
    }
    try:
        conn.execute("SET LOCAL statement_timeout = '15min'")
        # 2만 개의 1024차원 인덱스 생성은 64MB 기본 메모리를 넘는다.
        # 검증의 일괄 생성에만 메모리 여유를 주고 제품 검색 설정은 유지한다.
        conn.execute("SET LOCAL maintenance_work_mem = '256MB'")
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("SET LOCAL search_path TO {}, public").format(
                sql.Identifier(schema)
            )
        )
        conn.execute("SELECT '[0]'::vector")
        for path in migration_files():
            # 확장은 검증 스키마에 새로 만들지 않고 기존 public의 타입·연산자를 쓴다.
            if path.name.endswith("_extensions.sql"):
                continue
            conn.execute(path.read_text())
        conn.execute("DROP INDEX idx_chunks_embedding")
        with conn.cursor().copy(
            "COPY documents(id,title,content_type,content,content_hash,owner_id,visibility,tags) FROM STDIN"
        ) as copy:
            for i, did in enumerate(doc_ids):
                text = f"OpenArchive benchmark document {i}: DB triggers, embeddings and permissions."
                copy.write_row(
                    (
                        did,
                        f"benchmark-{i}",
                        "md" if i % 2 else "txt",
                        text,
                        hashlib.sha256(text.encode()).hexdigest(),
                        "other-owner",
                        "private" if i % 5 == 0 else "public",
                        [f"group-{i % 10}"],
                    )
                )
        start = time.perf_counter()
        with conn.cursor().copy(
            "COPY document_chunks(document_id,version,chunk_index,content,embedding) FROM STDIN"
        ) as copy:
            for i, did in enumerate(doc_ids):
                for index in range(chunks_per_document):
                    vector = [rng.uniform(-1, 1) for _ in range(1024)]
                    norm = math.sqrt(sum(x * x for x in vector))
                    vector = [x / norm for x in vector]
                    if index == 0 and len(vectors) < queries:
                        vectors.append(to_pgvector_literal(vector))
                    copy.write_row(
                        (
                            did,
                            1,
                            index,
                            f"benchmark chunk {i}-{index}",
                            to_pgvector_literal(vector),
                        )
                    )
        report["copy_seconds"] = time.perf_counter() - start
        report["index_build_maintenance_work_mem"] = conn.execute(
            "SHOW maintenance_work_mem"
        ).fetchone()[0]
        print("Fixture copied; building production HNSW index", flush=True)
        start = time.perf_counter()
        conn.execute(
            (ROOT / "backend/openarchive/migrations/004_indexes.sql").read_text()
        )
        report["index_build_seconds"] = time.perf_counter() - start
        conn.execute("ANALYZE documents")
        conn.execute("ANALYZE document_chunks")
        count, distinct = conn.execute(
            "SELECT count(*),count(DISTINCT embedding::text) FROM document_chunks"
        ).fetchone()
        assert count == distinct == documents * chunks_per_document
        report["fixture"] = {
            "schema": schema,
            "documents": documents,
            "chunks": count,
            "distinct_vectors": distinct,
            "relations": 0,
        }
        conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
        conn.execute("SET LOCAL random_page_cost = 1.1")
        conn.execute("SET LOCAL jit = off")
        conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
        report["server"] = conn.execute(
            "SELECT version(),pg_is_in_recovery()"
        ).fetchone()
        for vector in vectors:
            for label, filters in [
                ("broad", {}),
                ("tag", {"tags": ["group-1"]}),
                ("type", {"ctype": "md"}),
            ]:
                params = {
                    "qvec": vector,
                    "query": "benchmark search",
                    "identifier": None,
                    "edition": None,
                    "tags": None,
                    "ctype": None,
                    "folder": None,
                    "user": "benchmark-reader",
                    "k": 10,
                } | filters
                observation = measure_query(conn, params)
                report["queries"].append({"filter": label, **observation})
                print(
                    label,
                    round(observation["hnsw_ms"]),
                    "ms; HNSW:",
                    observation["hnsw_used"],
                    "documents:",
                    observation["direct_documents"],
                    flush=True,
                )
    finally:
        conn.rollback()
    report["cleaned_up"] = conn.execute(
        "SELECT to_regnamespace(%s) IS NULL", (schema,)
    ).fetchone()[0]
    conn.rollback()
    overlaps = [
        q["document_overlap"]
        for q in report["queries"]
        if q["document_overlap"] is not None
    ]
    report["summary"] = {
        "hnsw_plans": sum(q["hnsw_used"] for q in report["queries"]),
        "queries": len(report["queries"]),
        "median_hnsw_ms": statistics.median(q["hnsw_ms"] for q in report["queries"]),
        "median_exact_ms": statistics.median(q["exact_ms"] for q in report["queries"]),
        "mean_document_overlap": statistics.mean(overlaps) if overlaps else None,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=500)
    parser.add_argument("--chunks-per-document", type=int, default=40)
    parser.add_argument("--queries", type=int, default=5)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if (
        min(args.documents, args.chunks_per_document, args.queries) < 1
        or args.queries > args.documents
    ):
        parser.error("개수는 양의 정수이며 queries <= documents여야 합니다")
    with psycopg.connect(get_settings().database_url) as conn:
        report = run_benchmark(
            conn,
            documents=args.documents,
            chunks_per_document=args.chunks_per_document,
            queries=args.queries,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    print(report["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
