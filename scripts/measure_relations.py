#!/usr/bin/env python3
"""실데이터에서 관계 edge 판정 신호와 HNSW 비용을 재현한다."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import psycopg

DEFAULT_DATABASE_URL = "postgresql://openarchive:openarchive@localhost:5433/openarchive"
NEIGHBOR_COUNTS = (5, 10, 20, 40)


def explain(conn: psycopg.Connection, query: str, params: tuple[Any, ...]) -> dict[str, Any]:
    with conn.transaction():
        conn.execute("SET LOCAL hnsw.ef_search = 200")
        conn.execute("SET LOCAL random_page_cost = 1.1")
        row = conn.execute(
            f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}", params
        ).fetchone()
    return row[0][0]


def plan_uses_hnsw(plan: dict[str, Any]) -> bool:
    encoded = json.dumps(plan)
    return "Index Scan" in encoded and "idx_chunks_embedding" in encoded


def fetch_vector_query(
    conn: psycopg.Connection, query: str, params: tuple[Any, ...]
) -> list[tuple[Any, ...]]:
    """벡터 정렬을 쓰는 측정은 두 SET LOCAL이 적용된 같은 트랜잭션에서 실행한다."""
    with conn.transaction():
        conn.execute("SET LOCAL hnsw.ef_search = 200")
        conn.execute("SET LOCAL random_page_cost = 1.1")
        return conn.execute(query, params).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    parser.add_argument("--ratio-neighbors", type=int, default=10)
    args = parser.parse_args()
    if args.ratio_neighbors not in NEIGHBOR_COUNTS:
        parser.error(f"--ratio-neighbors는 {NEIGHBOR_COUNTS} 중 하나여야 합니다")

    neighbor_sql = """
    SELECT me.id, nb.id, nb.document_id, nb.dist
    FROM document_chunks me
    JOIN documents mine ON mine.id = me.document_id AND mine.owner_id = 'seed'
    CROSS JOIN LATERAL (
        SELECT c.id, c.document_id, c.embedding <=> me.embedding AS dist
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id AND d.owner_id = 'seed'
        WHERE c.document_id <> me.document_id
        ORDER BY c.embedding <=> me.embedding
        LIMIT %s
    ) nb
    WHERE (%s::uuid IS NULL OR me.document_id = %s::uuid)
    """

    with psycopg.connect(args.database_url) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        environment = conn.execute(
            """
            SELECT count(DISTINCT d.id), count(c.id), count(DISTINCT c.embedding::text)
            FROM documents d JOIN document_chunks c ON c.document_id = d.id
            WHERE d.owner_id = 'seed'
            """
        ).fetchone()
        representative = conn.execute(
            """
            SELECT d.id, d.title, count(c.id) AS chunks
            FROM documents d JOIN document_chunks c ON c.document_id = d.id
            WHERE d.owner_id = 'seed'
            GROUP BY d.id, d.title
            ORDER BY abs(count(c.id) - 4), d.title
            LIMIT 1
            """
        ).fetchone()
        print(f"environment documents={environment[0]} chunks={environment[1]} "
              f"distinct_embeddings={environment[2]}")
        print(f"representative title={representative[1]!r} chunks={representative[2]} "
              f"id={representative[0]}")

        for n in NEIGHBOR_COUNTS:
            full = explain(conn, neighbor_sql, (n, None, None))
            one = explain(conn, neighbor_sql, (n, representative[0], representative[0]))
            spread = fetch_vector_query(
                conn,
                """
                WITH neighbors AS (
                    SELECT me.id AS source_chunk, nb.document_id
                    FROM document_chunks me
                    JOIN documents mine ON mine.id = me.document_id AND mine.owner_id = 'seed'
                    CROSS JOIN LATERAL (
                        SELECT c.document_id
                        FROM document_chunks c
                        JOIN documents d ON d.id = c.document_id AND d.owner_id = 'seed'
                        WHERE c.document_id <> me.document_id
                        ORDER BY c.embedding <=> me.embedding
                        LIMIT %s
                    ) nb
                )
                SELECT avg(document_count), min(document_count), max(document_count)
                FROM (
                    SELECT source_chunk, count(DISTINCT document_id) AS document_count
                    FROM neighbors GROUP BY source_chunk
                ) counts
                """,
                (n,),
            )[0]
            print(
                f"N={n} full_ms={full['Execution Time']:.3f} "
                f"full_hnsw={plan_uses_hnsw(full)} one_ms={one['Execution Time']:.3f} "
                f"one_hnsw={plan_uses_hnsw(one)} distinct_docs_per_chunk="
                f"{float(spread[0]):.2f}/{spread[1]}/{spread[2]}"
            )

        ratios = fetch_vector_query(
            conn,
            """
            WITH matches AS (
                SELECT me.document_id AS source_id, nb.document_id AS target_id,
                       count(DISTINCT me.id) AS matched_chunks
                FROM document_chunks me
                JOIN documents mine ON mine.id = me.document_id AND mine.owner_id = 'seed'
                CROSS JOIN LATERAL (
                    SELECT c.document_id
                    FROM document_chunks c
                    JOIN documents d ON d.id = c.document_id AND d.owner_id = 'seed'
                    WHERE c.document_id <> me.document_id
                    ORDER BY c.embedding <=> me.embedding
                    LIMIT %s
                ) nb
                GROUP BY me.document_id, nb.document_id
            ), chunk_counts AS (
                SELECT c.document_id, count(*) AS chunks
                FROM document_chunks c JOIN documents d ON d.id = c.document_id
                WHERE d.owner_id = 'seed' GROUP BY c.document_id
            )
            SELECT width_bucket(matched_chunks::numeric / chunks, 0, 1.000001, 10) AS bucket,
                   count(*)
            FROM matches JOIN chunk_counts ON document_id = source_id
            GROUP BY bucket ORDER BY bucket
            """,
            (args.ratio_neighbors,),
        )
        print(f"ratio_histogram N={args.ratio_neighbors} " + " ".join(
            f"{(bucket - 1) / 10:.1f}-{bucket / 10:.1f}:{count}" for bucket, count in ratios
        ))

        ratio_examples = fetch_vector_query(
            conn,
            """
            WITH matches AS (
                SELECT me.document_id AS source_id, nb.document_id AS target_id,
                       count(DISTINCT me.id) AS matched_chunks
                FROM document_chunks me
                JOIN documents mine ON mine.id = me.document_id AND mine.owner_id = 'seed'
                CROSS JOIN LATERAL (
                    SELECT c.document_id
                    FROM document_chunks c
                    JOIN documents d ON d.id = c.document_id AND d.owner_id = 'seed'
                    WHERE c.document_id <> me.document_id
                    ORDER BY c.embedding <=> me.embedding
                    LIMIT %s
                ) nb
                GROUP BY me.document_id, nb.document_id
            ), chunk_counts AS (
                SELECT document_id, count(*) AS chunks FROM document_chunks GROUP BY document_id
            )
            SELECT a.title, b.title, matched_chunks, chunks,
                   matched_chunks::numeric / chunks AS ratio
            FROM matches m
            JOIN chunk_counts cc ON cc.document_id = m.source_id
            JOIN documents a ON a.id = m.source_id
            JOIN documents b ON b.id = m.target_id
            WHERE (a.title LIKE 'ADR-011:%%' AND b.title IN
                     ('검색 데이터 흐름', '프로젝트: OpenSQL AI 문서관리 플랫폼'))
               OR (a.title = '검색 데이터 흐름' AND b.title LIKE 'ADR-011:%%')
               OR (a.title = '프로젝트: OpenSQL AI 문서관리 플랫폼'
                   AND b.title LIKE 'ADR-011:%%')
               OR (a.title LIKE 'ADR-018:%%' AND b.title = '관련 문서·태그 추천')
               OR (a.title = '관련 문서·태그 추천' AND b.title LIKE 'ADR-018:%%')
            ORDER BY a.title, b.title
            """,
            (args.ratio_neighbors,),
        )
        for source, target, matched, chunks, ratio in ratio_examples:
            print(f"ratio {source} -> {target} matched={matched}/{chunks} ratio={float(ratio):.3f}")

        named_pairs = conn.execute(
            """
            WITH pairs(a_pattern, b_pattern, label) AS (VALUES
                ('프로젝트: OpenSQL AI 문서관리 플랫폼%%', 'ADR-011:%%', 'CLAUDE↔ADR-011'),
                ('프로젝트: OpenSQL AI 문서관리 플랫폼%%', 'ADR-018:%%', 'CLAUDE↔ADR-018'),
                ('ADR-011:%%', '검색 데이터 흐름', 'ADR-011↔검색 데이터 흐름'),
                ('ADR-018:%%', '관련 문서·태그 추천', 'ADR-018↔관련 문서·태그 추천'),
                ('ADR-011:%%', 'OpenSQL 개발 환경 구축', '무관: ADR-011↔SETUP')
            ), resolved AS (
                SELECT p.label, a.content AS a_content, b.content AS b_content
                FROM pairs p
                JOIN documents a ON a.owner_id = 'seed' AND a.title LIKE p.a_pattern
                JOIN documents b ON b.owner_id = 'seed' AND b.title LIKE p.b_pattern
            )
            SELECT label,
                   word_similarity(a_content, b_content) AS a_in_b,
                   word_similarity(b_content, a_content) AS b_in_a
            FROM resolved ORDER BY label
            """
        ).fetchall()
        for label, a_in_b, b_in_a in named_pairs:
            print(f"word_similarity {label} a_in_b={a_in_b:.6f} b_in_a={b_in_a:.6f} "
                  f"margin={abs(a_in_b - b_in_a):.6f}")

        margins = conn.execute(
            """
            WITH scores AS (
                SELECT word_similarity(a.content, b.content) AS ab,
                       word_similarity(b.content, a.content) AS ba
                FROM documents a CROSS JOIN documents b
                WHERE a.owner_id = 'seed' AND b.owner_id = 'seed' AND a.id < b.id
            )
            SELECT count(*),
                   count(*) FILTER (WHERE abs(ab - ba) < 0.1),
                   percentile_cont(ARRAY[0.5, 0.75, 0.9, 0.95])
                     WITHIN GROUP (ORDER BY abs(ab - ba))
            FROM scores
            """
        ).fetchone()
        print(f"margin_distribution pairs={margins[0]} below_0.1={margins[1]} "
              f"percentiles={margins[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
