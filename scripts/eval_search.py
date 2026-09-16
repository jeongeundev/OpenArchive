#!/usr/bin/env python3
"""검색 평가셋으로 Recall@k·MRR을 잰다 (#94).

    DATABASE_URL=… EMBEDDING_PROVIDER=local \\
      python scripts/eval_search.py scripts/eval/c.json [--label base] [--out FILE]

평가셋은 `{"queries": [{"query": …, "relevant": [제목, …]}, …]}`다. 정답은 제목으로 적고
실행 시점의 DB에서 문서 id로 푼다 — 동명 문서는 전부 정답이고, 없는 제목은 오타로 보고
멈춘다. 검색은 `search_documents`를 화면과 같은 k=10으로 호출하며, 결과 순서(직접 히트가
앞, 관계 확장이 뒤)를 문서 단위로 접어 순위로 쓴다.

실 `BAAI/bge-m3`로만 잰다. `fake` 프로바이더의 수치는 의미가 없으므로 실행을 거부한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import psycopg

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import get_settings
from app.embeddings import get_provider
from app.services.search import search_documents

SEARCH_K = 10
DEFAULT_KS = (1, 5, 10)


@dataclass(frozen=True)
class QueryResult:
    query: str
    ranked: list[UUID]
    relevant: set[UUID]
    via_hits: set[UUID]
    top_titles: list[str] = field(default_factory=list)

    @property
    def first_relevant_rank(self) -> int | None:
        for position, document_id in enumerate(self.ranked, start=1):
            if document_id in self.relevant:
                return position
        return None

    @property
    def relevant_only_via(self) -> set[UUID]:
        return self.relevant & self.via_hits


def rank_documents(document_ids: list[UUID]) -> list[UUID]:
    """결과 행 순서를 지키며 같은 문서의 재등장을 지운다."""
    return list(dict.fromkeys(document_ids))


def recall_at_k(ranked: list[UUID], relevant: set[UUID], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked: list[UUID], relevant: set[UUID]) -> float:
    for position, document_id in enumerate(ranked, start=1):
        if document_id in relevant:
            return 1.0 / position
    return 0.0


def resolve_relevant(
    titles: list[str], title_to_ids: dict[str, list[UUID]]
) -> set[UUID]:
    missing = [title for title in titles if title not in title_to_ids]
    if missing:
        raise KeyError(f"평가셋에 코퍼스에 없는 제목이 있다: {missing}")
    return {document_id for title in titles for document_id in title_to_ids[title]}


def summarize(results: list[QueryResult], ks: tuple[int, ...] = DEFAULT_KS) -> dict:
    count = len(results)
    summary: dict = {"queries": count}
    for k in ks:
        summary[f"recall@{k}"] = (
            sum(recall_at_k(r.ranked, r.relevant, k) for r in results) / count
        )
    summary["mrr"] = sum(reciprocal_rank(r.ranked, r.relevant) for r in results) / count
    summary["top1_hits"] = sum(1 for r in results if r.first_relevant_rank == 1)
    summary["misses"] = sum(1 for r in results if r.first_relevant_rank is None)
    summary["via_only"] = sum(1 for r in results if r.relevant_only_via)
    return summary


async def evaluate(evalset: dict, dsn: str) -> list[QueryResult]:
    provider = get_provider()
    results: list[QueryResult] = []
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        rows = await (await conn.execute("SELECT id, title FROM documents")).fetchall()
        title_to_ids: dict[str, list[UUID]] = {}
        for document_id, title in rows:
            title_to_ids.setdefault(title, []).append(document_id)
        titles_by_id = {document_id: title for document_id, title in rows}

        for item in evalset["queries"]:
            relevant = resolve_relevant(item["relevant"], title_to_ids)
            hits = await search_documents(conn, provider, query=item["query"], k=SEARCH_K)
            ranked = rank_documents([hit.document_id for hit in hits])
            via_hits = {hit.document_id for hit in hits if hit.via is not None}
            results.append(
                QueryResult(
                    query=item["query"],
                    ranked=ranked,
                    relevant=relevant,
                    via_hits=via_hits,
                    top_titles=[titles_by_id[document_id] for document_id in ranked[:3]],
                )
            )
    return results


def render(results: list[QueryResult], summary: dict) -> str:
    lines = []
    for result in results:
        rank = result.first_relevant_rank
        mark = "  " if rank == 1 else ("v " if result.relevant_only_via else "x " if rank is None else "  ")
        lines.append(
            f"{mark}{rank if rank is not None else '-':>2}  {result.query}"
            f"  →  {' / '.join(result.top_titles)}"
        )
    lines.append("")
    lines.append(
        "  ".join(
            f"{key}={value:.3f}" if isinstance(value, float) else f"{key}={value}"
            for key, value in summary.items()
        )
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("evalset", type=Path)
    parser.add_argument("--label", default="base", help="결과 파일에 남길 표식 (예: base, after-r1)")
    parser.add_argument("--out", type=Path, help="질의별 상세와 요약을 JSON으로 저장할 경로")
    args = parser.parse_args()

    settings = get_settings()
    if settings.embedding_provider != "local":
        sys.exit(
            f"EMBEDDING_PROVIDER={settings.embedding_provider!r}: 실 BGE-M3(local)로만 잰다. "
            "fake 벡터의 Recall은 의미가 없다."
        )

    evalset = json.loads(args.evalset.read_text())
    results = asyncio.run(evaluate(evalset, settings.database_url))
    summary = summarize(results)
    print(render(results, summary))

    if args.out:
        payload = {
            "label": args.label,
            "evalset": str(args.evalset),
            "summary": summary,
            "queries": [
                {
                    "query": r.query,
                    "first_relevant_rank": r.first_relevant_rank,
                    "top_titles": r.top_titles,
                    "relevant_only_via": [str(i) for i in r.relevant_only_via],
                }
                for r in results
            ],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
