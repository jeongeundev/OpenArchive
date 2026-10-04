#!/usr/bin/env python3
"""검색 평가셋으로 문서 순위와 고정 근거 포함을 잰다.

    DATABASE_URL=… EMBEDDING_PROVIDER=local \\
      python scripts/eval_search.py scripts/eval/c.json [--label base] [--out FILE]

평가셋은 `{"queries": [{"query": …, "relevant": [제목, …]}, …]}`다. 정답은 제목으로 적고
실행 시점의 DB에서 문서 id로 푼다 — 동명 문서는 전부 정답이고, 없는 제목은 오타로 보고
멈춘다. 검색은 `search_documents`를 화면과 같은 k=10으로 호출하며, 결과 순서(직접 히트가
앞, 관계 확장이 뒤)를 문서 단위로 접어 순위로 쓴다.

선택 `evidence: [{"source": 제목, "text": 고정 근거 문장}]`가 있으면 직접 결과의 첫 발췌·
첫 미리보기·상위 5문서의 본문 후보에 모든 근거가 포함되는지 따로 센다. 공백을 정규화한
문자열 포함 지표이며 의미 정확도가 아니다. --user·--tag는 제목 해석과 검색에 동일 적용한다.
근거가 있으면 실패 원인도 나눈다 — 근거가 출처 문서의 저장된 본문에 없으면 추출(`extraction`),
상위 5문서 후보에 없으면 후보 부족(`candidates`), 후보에는 있으나 첫 발췌에 없으면 선택(`selection`)이다.

`relevant`가 빈 질문(근거 없음·권한·과거 버전)은 순위 지표에서 빼고 최고 점수만 남긴다.
선택 `absent: [문장]`은 어떤 결과 자리(관계 확장 포함)에도 나오면 안 되는 문장이다(권한).
`absent_current`는 현재 판 결과에만 나오면 안 되는 과거 판본 문장이다 — 「이전 버전」(via=revision)
자리에 표시돼 나오는 것은 설계이므로 누출로 세지 않고, 거기서 찾히는지를 따로 기록한다.

실 `BAAI/bge-m3`로만 잰다. `fake` 프로바이더의 수치는 의미가 없으므로 실행을 거부한다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import psycopg

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from openarchive.config import get_settings
from openarchive.embeddings import get_provider
from openarchive.services.search import EXCERPT_LENGTH, SearchHit, search_documents
from openarchive.services.visibility import VISIBLE_TO_USER

SEARCH_K = 10
DEFAULT_KS = (1, 5, 10)


@dataclass(frozen=True)
class QueryResult:
    query: str
    ranked: list[UUID]
    relevant: set[UUID]
    via_hits: set[UUID]
    top_titles: list[str] = field(default_factory=list)
    evidence: dict | None = None
    preview_diagnosis: dict | None = None
    failure_cause: str | None = None
    leaks: list[str] = field(default_factory=list)
    top_score: float | None = None
    past_in_revisions: list[str] = field(default_factory=list)

    @property
    def first_relevant_rank(self) -> int | None:
        for position, document_id in enumerate(self.ranked, start=1):
            if document_id in self.relevant:
                return position
        return None

    @property
    def relevant_only_via(self) -> set[UUID]:
        return self.relevant & self.via_hits


def grade_evidence(hits: list[SearchHit], evidence: list[dict] | None) -> dict | None:
    """고정 출처의 모든 문장 포함을 센다. 공백만 정규화하며 의미 정확도를 채점하지 않는다."""
    if evidence is None:
        return None
    if not evidence or any(not item["text"].strip() or not item["source"].strip() for item in evidence):
        raise ValueError("근거는 빈 목록·문장·출처일 수 없다")
    direct = [hit for hit in hits if hit.via is None]
    modes = {
        "first_excerpt": [(hit.title, hit.content) for hit in direct[:1]],
        "first_preview": [(hit.title, hit.preview or "") for hit in direct[:1]],
        "top5_passages": [(hit.title, passage.content) for hit in direct[:5] for passage in hit.passages],
    }
    result = {}
    for mode, contents in modes.items():
        missing = [item for item in evidence if not any(
            title == item["source"] and " ".join(item["text"].split()) in " ".join(content.split())
            for title, content in contents
        )]
        result[mode] = {
            "complete": not missing, "matched": len(evidence) - len(missing),
            "required": len(evidence), "missing": missing,
        }
    return result


def _normalize(text: str) -> str:
    return " ".join(text.split())


def classify_failure(
    evidence: list[dict] | None, stored: dict[str, list[str]], graded: dict | None
) -> str | None:
    """실패 위치를 추출 → 후보 → 선택 순으로 가린다. 같은 제목 문서 중 하나에라도 있으면 추출된 것이다."""
    if evidence is None:
        return None
    for item in evidence:
        text = _normalize(item["text"])
        if not any(text in _normalize(content) for content in stored.get(item["source"], [])):
            return "extraction"
    if graded["first_excerpt"]["complete"]:
        return "complete"
    if not graded["top5_passages"]["complete"]:
        return "candidates"
    return "selection"


def _found(hits: list[SearchHit], texts: list[str] | None) -> list[str]:
    contents = [
        _normalize(text)
        for hit in hits
        for text in (hit.content, hit.preview or "", *(passage.content for passage in hit.passages))
    ]
    return [item for item in texts or [] if any(_normalize(item) in content for content in contents)]


def find_leaks(hits: list[SearchHit], absent: list[str] | None, *, current_only: bool = False) -> list[str]:
    """나오면 안 되는 문장이 결과의 발췌·미리보기·후보에 있으면 돌려준다.

    `current_only`면 「이전 버전」 자리(via=revision)는 보지 않는다 — 과거 판본으로 표시된 결과다.
    """
    if current_only:
        hits = [hit for hit in hits if hit.via is None or hit.via.kind != "revision"]
    return _found(hits, absent)


def find_in_revisions(hits: list[SearchHit], texts: list[str] | None) -> list[str]:
    """과거 판본 문장이 「이전 버전」 자리에서 찾히는지."""
    return _found([hit for hit in hits if hit.via is not None and hit.via.kind == "revision"], texts)


def minimum_evidence_window(title: str, content: str, evidence: list[dict]) -> int | None:
    """한 본문에서 모든 근거를 담는 최소 연속 창의 원문 문자 수. 공백도 길이에 포함한다."""
    if any(item["source"] != title for item in evidence):
        return None
    occurrences = []
    for item in evidence:
        pattern = r"\s+".join(re.escape(word) for word in item["text"].split())
        matches = [(m.start(1), m.end(1)) for m in re.finditer(f"(?=({pattern}))", content)]
        if not matches:
            return None
        occurrences.append(matches)
    shortest = None
    for start in sorted({start for matches in occurrences for start, _ in matches}):
        ends = [min((end for begin, end in matches if begin >= start), default=None)
                for matches in occurrences]
        if all(end is not None for end in ends):
            length = max(ends) - start
            shortest = length if shortest is None else min(shortest, length)
    return shortest


def diagnose_preview(hits: list[SearchHit], evidence: list[dict] | None) -> dict | None:
    """고정 근거로 실패 위치만 진단한다. 정답을 사용해 검색 결과를 선택하지 않는다."""
    graded = grade_evidence(hits, evidence)
    if graded is None:
        return None
    direct = [hit for hit in hits if hit.via is None]
    excerpt = None
    passage = None
    if direct:
        hit = direct[0]
        excerpt = minimum_evidence_window(hit.title, hit.content, evidence)
        lengths = [minimum_evidence_window(hit.title, p.content, evidence) for p in hit.passages]
        passage = min((length for length in lengths if length is not None), default=None)
    if graded["first_preview"]["complete"]:
        reason = "complete"
    elif excerpt is not None and excerpt <= EXCERPT_LENGTH:
        reason = "selection"
    elif passage is not None and passage <= EXCERPT_LENGTH:
        reason = "scope"
    elif excerpt is not None or passage is not None:
        reason = "length"
    else:
        reason = "missing"
    return {"reason": reason, "first_excerpt_minimum_chars": excerpt,
            "first_document_passage_minimum_chars": passage}


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
    answerable = [r for r in results if r.relevant]
    no_answer = len(results) - len(answerable)
    count = len(answerable)
    summary: dict = {"queries": len(results)}
    if no_answer:
        summary["answerable_queries"] = count
    for k in ks:
        summary[f"recall@{k}"] = (
            sum(recall_at_k(r.ranked, r.relevant, k) for r in answerable) / count
        )
    summary["mrr"] = sum(reciprocal_rank(r.ranked, r.relevant) for r in answerable) / count
    summary["top1_hits"] = sum(1 for r in answerable if r.first_relevant_rank == 1)
    summary["misses"] = sum(1 for r in answerable if r.first_relevant_rank is None)
    summary["via_only"] = sum(1 for r in answerable if r.relevant_only_via)
    if no_answer:
        summary["no_answer_queries"] = no_answer
        summary["leaks"] = sum(1 for r in results if r.leaks)
    for cause in ("complete", "selection", "candidates", "extraction"):
        tally = sum(1 for r in results if r.failure_cause == cause)
        if tally:
            summary[f"cause_{cause}"] = tally
    measured = [r.evidence for r in results if r.evidence is not None]
    if measured:
        summary["evidence_queries"] = len(measured)
        for mode in ("first_excerpt", "first_preview", "top5_passages"):
            summary[f"{mode}_complete"] = sum(metric[mode]["complete"] for metric in measured)
    return summary


async def evaluate(
    evalset: dict, dsn: str, *, user_id: str | None = None, tags: list[str] | None = None,
) -> list[QueryResult]:
    provider = get_provider()
    results: list[QueryResult] = []
    async with await psycopg.AsyncConnection.connect(dsn, prepare_threshold=None) as conn:
        rows = await (await conn.execute(
            f"SELECT d.id, d.title, d.content FROM documents d WHERE {VISIBLE_TO_USER} "
            "AND (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)",
            {"user": user_id, "tags": tags or None},
        )).fetchall()
        title_to_ids: dict[str, list[UUID]] = {}
        stored: dict[str, list[str]] = {}
        for document_id, title, content in rows:
            title_to_ids.setdefault(title, []).append(document_id)
            stored.setdefault(title, []).append(content)
        titles_by_id = {document_id: title for document_id, title, _ in rows}

        for item in evalset["queries"]:
            relevant = resolve_relevant(item["relevant"], title_to_ids)
            evidence = item.get("evidence")
            if evidence is not None:
                grade_evidence([], evidence)
                resolve_relevant([fragment["source"] for fragment in evidence], title_to_ids)
            hits = await search_documents(
                conn, provider, query=item["query"], k=SEARCH_K, user_id=user_id, tags=tags,
            )
            ranked = rank_documents([hit.document_id for hit in hits])
            via_hits = {hit.document_id for hit in hits if hit.via is not None}
            graded = grade_evidence(hits, evidence)
            results.append(
                QueryResult(
                    query=item["query"],
                    ranked=ranked,
                    relevant=relevant,
                    via_hits=via_hits,
                    top_titles=[titles_by_id[document_id] for document_id in ranked[:3]],
                    evidence=graded,
                    preview_diagnosis=diagnose_preview(hits, evidence),
                    failure_cause=classify_failure(evidence, stored, graded),
                    leaks=find_leaks(hits, item.get("absent"))
                    + find_leaks(hits, item.get("absent_current"), current_only=True),
                    past_in_revisions=find_in_revisions(hits, item.get("absent_current")),
                    top_score=hits[0].score if hits else None,
                )
            )
    return results


def render(results: list[QueryResult], summary: dict) -> str:
    lines = []
    for result in results:
        rank = result.first_relevant_rank
        mark = "  " if rank == 1 else ("v " if result.relevant_only_via else "x " if rank is None else "  ")
        evidence_mark = "" if result.evidence is None else (
            f"  [근거: 발췌={int(result.evidence['first_excerpt']['complete'])}"
            f" 미리보기={int(result.evidence['first_preview']['complete'])}"
            f" 후보={int(result.evidence['top5_passages']['complete'])}]"
        )
        diagnosis_mark = "" if result.preview_diagnosis is None else (
            f"  [미리보기 원인={result.preview_diagnosis['reason']}]"
        )
        if result.failure_cause is not None:
            diagnosis_mark += f"  [원인={result.failure_cause}]"
        if not result.relevant:
            mark = "! " if result.leaks else "0 "
            diagnosis_mark += f"  [정답 없음 최고점={result.top_score}]"
            if result.leaks:
                diagnosis_mark += f"  [누출={result.leaks}]"
            if result.past_in_revisions:
                diagnosis_mark += "  [과거 값은 이전 버전 자리에 있음]"
        lines.append(
            f"{mark}{rank if rank is not None else '-':>2}  {result.query}"
            f"  →  {' / '.join(result.top_titles)}{evidence_mark}{diagnosis_mark}"
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
    parser.add_argument("--user", help="검색에 전달할 사용자 주체 (생략하면 public 문서만)")
    parser.add_argument("--tag", action="append", help="평가 코퍼스의 태그 (반복 지정 가능)")
    parser.add_argument("--label", default="base", help="결과 파일에 남길 표식 (예: base, after-r1)")
    parser.add_argument("--out", type=Path, help="질의별 상세와 요약을 JSON으로 저장할 경로")
    args = parser.parse_args()

    settings = get_settings()
    if settings.embedding_provider != "local":
        sys.exit(
            f"EMBEDDING_PROVIDER={settings.embedding_provider!r}: 실 BGE-M3(local)로만 잰다. "
            "fake 벡터의 Recall은 의미가 없다."
        )

    evalset_bytes = args.evalset.read_bytes()
    evalset = json.loads(evalset_bytes)
    results = asyncio.run(evaluate(evalset, settings.database_url, user_id=args.user, tags=args.tag))
    summary = summarize(results)
    print(render(results, summary))

    if args.out:
        payload = {
            "label": args.label,
            "evalset": str(args.evalset),
            "evalset_sha256": hashlib.sha256(evalset_bytes).hexdigest(),
            "k": SEARCH_K,
            "user": args.user,
            "tags": args.tag or [],
            "summary": summary,
            "queries": [
                {
                    "query": r.query,
                    "first_relevant_rank": r.first_relevant_rank,
                    "top_titles": r.top_titles,
                    "relevant_only_via": [str(i) for i in r.relevant_only_via],
                    **({"evidence": r.evidence} if r.evidence is not None else {}),
                    **({"preview_diagnosis": r.preview_diagnosis} if r.preview_diagnosis is not None else {}),
                    **({"failure_cause": r.failure_cause} if r.failure_cause is not None else {}),
                    **({"leaks": r.leaks, "top_score": r.top_score,
                        "past_in_revisions": r.past_in_revisions} if not r.relevant else {}),
                }
                for r in results
            ],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
