#!/usr/bin/env python3
"""근거 기반 답변(ADR-043)을 평가셋으로 잰다 — 모델 확정(#96 c)과 #168 전후 비교의 기준선.

    DATABASE_URL=… EMBEDDING_PROVIDER=local ANSWER_PROVIDER=ollama ANSWER_MODEL=qwen3:8b \\
      python scripts/eval_ask.py scripts/eval/formats.json --user eval167 --tag eval167 \\
      [--repeats 3] [--out FILE]

질문마다 `/api/ask`와 같은 서비스 경로(`gather_evidence` → `generate_answer`)를 부른다. 근거는 한 번
모으고 생성만 `--repeats`번 되풀이해 지연 분포를 얻는다 — 온도 0이라 답은 같아야 하며, 다르면
`answers_differ`로 남긴다. Ollama는 같은 프롬프트 앞부분의 KV 캐시를 재사용해 반복이 10배 빨라지고
(실측 16초 → 1.5초), 같은 문서를 근거로 받는 바로 앞 질문도 캐시를 남긴다. 다른 프롬프트로는 밀려나지
않아, 생성마다 모델을 내렸다(`keep_alive: 0`) 짧은 요청으로 다시 올린 뒤 생성만 잰다 — 처음 받는 질문의 지연이다.

자동 채점은 공백·천 단위 쉼표를 무시한 문자열 포함이며 의미 정확도가 아니다.
- `answer_keys`: 답에 모두 있어야 하는 핵심값. 원소가 목록이면 그중 하나(같은 값의 다른 표기).
- `keys_in_sources`: 핵심값이 모델에 준 근거에 있었는지. 없으면 추출·검색·글자 예산의 몫이고, 있는데 답에
  없으면 모델의 몫이다(`model_misses`).
- `relevant`의 문서를 근거로 받았는지(`given_relevant`, 검색 몫)와 인용했는지(`cited_relevant`, 모델 몫).
- 거절: 근거 0건(`no_evidence`)이거나 답에 거절 문구가 있으면. 정답 근거가 있는 질문의 거절은 실패로 센다.
- `answer_absent`: 답에 나오면 안 되는 문장(권한 누출).
- `answer_from_revision`: 핵심값을 「이전 버전」 근거(revised)로 인용해야 한다 — 과거 값을 현재 판 근거로
  말하면 과거를 현재로 말한 것이다.
주장과 인용 대목의 일치·근거 누락·근거 없는 주장은 사람이 결과 JSON의 `manual` 칸에 판정한다.

실 `BAAI/bge-m3`와 Ollama로만 잰다. fake 프로바이더의 수치는 모델 품질이 아니므로 실행을 거부한다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval_search import resolve_relevant

from openarchive.answers import AnswerProvider, get_answer_provider
from openarchive.config import get_settings
from openarchive.db import connect
from openarchive.embeddings import EmbeddingProvider, get_provider
from openarchive.services.answer import AnswerSource, gather_evidence, generate_answer
from openarchive.services.visibility import VISIBLE_TO_USER

REFUSAL = "근거 문서에서 찾을 수 없습니다"


def _normalize(text: str) -> str:
    return "".join(text.split()).replace(",", "")


def _contains(key: str | list[str], text: str) -> bool:
    """`text`는 이미 정규화된 문자열이다. 목록 키는 그중 하나만 있으면 된다."""
    return any(_normalize(option) in text for option in (key if isinstance(key, list) else [key]))


def grade_answer(
    item: dict, status: str, answer: str | None, sources: list[AnswerSource], relevant: set[UUID],
) -> dict:
    text = _normalize(answer or "")
    keys = item.get("answer_keys") or []
    missing = [key for key in keys if not _contains(key, text)]
    given = _normalize(" ".join(source.content for source in sources))
    cited = [source for source in sources if source.cited]
    from_revision = None
    if item.get("answer_from_revision"):
        from_revision = not missing and any(
            source.revised and all(_contains(key, _normalize(source.content)) for key in keys)
            for source in cited
        )
    return {
        "status": status,
        "key_hit": (not missing) if keys else None,
        "missing_keys": missing,
        "keys_in_sources": all(_contains(key, given) for key in keys) if keys else None,
        "given_relevant": any(s.document_id in relevant for s in sources) if relevant else None,
        "cited_relevant": any(s.document_id in relevant for s in cited) if relevant else None,
        "cited_outside": [s.title for s in cited if s.document_id not in relevant] if relevant else [],
        "refused": status == "no_evidence" or _normalize(REFUSAL) in text,
        "leaks": [sentence for sentence in item.get("answer_absent", []) if _normalize(sentence) in text],
        "revision_cited": from_revision,
    }


def percentile(values: list[float], p: float) -> float:
    """최근접 순위(nearest-rank) 백분위수 — 표본이 작아도 실제 관측값 하나를 돌려준다."""
    ordered = sorted(values)
    return ordered[max(math.ceil(p / 100 * len(ordered)) - 1, 0)]


def summarize(graded: list[dict]) -> dict:
    answerable = [g for g in graded if g["relevant"]]
    no_answer = [g for g in graded if not g["relevant"]]
    latencies = [value for g in graded for value in g["latencies"]]
    summary: dict = {"queries": len(graded)}
    for status in ("answered", "no_evidence", "failed", "disabled"):
        tally = sum(1 for g in graded if g["status"] == status)
        if tally:
            summary[f"status_{status}"] = tally
    summary.update({
        "answerable": len(answerable),
        "key_graded": sum(1 for g in answerable if g["key_hit"] is not None),
        "key_hits": sum(1 for g in answerable if g["key_hit"]),
        "keys_in_sources": sum(1 for g in answerable if g["keys_in_sources"]),
        "model_misses": sum(1 for g in answerable if g["keys_in_sources"] and not g["key_hit"]),
        "given_relevant": sum(1 for g in answerable if g["given_relevant"]),
        "cited_relevant": sum(1 for g in answerable if g["cited_relevant"]),
        "false_refusals": sum(1 for g in answerable if g["refused"]),
        "no_answer": len(no_answer),
        "correct_refusals": sum(1 for g in no_answer if g["refused"]),
        "leaks": sum(1 for g in graded if g["leaks"]),
        "revision_graded": sum(1 for g in graded if g["revision_cited"] is not None),
        "revision_cited": sum(1 for g in graded if g["revision_cited"]),
        "answers_differ": sum(1 for g in graded if g["answers_differ"]),
        "generation_calls": len(latencies),
    })
    if latencies:
        summary["latency_p50_s"] = percentile(latencies, 50)
        summary["latency_p95_s"] = percentile(latencies, 95)
        summary["latency_max_s"] = max(latencies)
    return summary


def check_providers(embedding_provider: str, answer_provider: str) -> None:
    if embedding_provider != "local":
        sys.exit(f"EMBEDDING_PROVIDER={embedding_provider!r}: 실 BGE-M3(local)로만 잰다.")
    if answer_provider != "ollama":
        sys.exit(f"ANSWER_PROVIDER={answer_provider!r}: 실 Ollama 모델로만 잰다.")


def _source_payload(source: AnswerSource) -> dict:
    return {
        "label": source.label, "document_id": str(source.document_id), "title": source.title,
        "chunk_index": source.chunk_index, "based_on_version": source.based_on_version,
        "current_version": source.current_version, "revised": source.revised,
        "cited": source.cited, "content": source.content,
    }


def ollama_cache_reset(url: str, model: str) -> Callable[[], None]:
    """모델을 내리고 짧은 요청으로 다시 올린다 — 앞선 프롬프트의 KV 캐시가 남지 않고, 적재 시간도 재지 않는다."""
    def post(path: str, body: dict) -> None:
        request = urllib.request.Request(
            url.rstrip("/") + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            response.read()

    def reset() -> None:
        post("/api/generate", {"model": model, "keep_alive": 0})
        post("/api/chat", {"model": model, "stream": False, "think": False,
                           "messages": [{"role": "user", "content": "안녕"}]})
    return reset


async def evaluate(
    evalset: dict, dsn: str, *, embedding_provider: EmbeddingProvider, answer_provider: AnswerProvider,
    reset_cache: Callable[[], None], user_id: str | None, tags: list[str] | None,
    context_chars: int, repeats: int,
) -> list[dict]:
    results = []
    async with await connect(dsn) as conn:
        rows = await (await conn.execute(
            f"SELECT d.id, d.title FROM documents d WHERE {VISIBLE_TO_USER} "
            "AND (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)",
            {"user": user_id, "tags": tags or None},
        )).fetchall()
        title_to_ids: dict[str, list[UUID]] = {}
        for document_id, title in rows:
            title_to_ids.setdefault(title, []).append(document_id)

        for item in evalset["queries"]:
            relevant = resolve_relevant(item["relevant"], title_to_ids)
            evidence = await gather_evidence(
                conn, embedding_provider, query=item["query"], user_id=user_id, tags=tags,
                context_chars=context_chars,
            )
            await conn.rollback()  # 생성 동안 트랜잭션을 열어 두지 않는다(API와 같은 조건)
            answers, latencies = [], []
            for _ in range(repeats):
                if evidence.sources:  # 근거가 없으면 서비스가 모델을 부르지 않는다
                    await asyncio.to_thread(reset_cache)
                started = time.perf_counter()
                result = await generate_answer(evidence, answer_provider)
                if result.status in ("answered", "failed"):
                    latencies.append(round(time.perf_counter() - started, 3))
                answers.append(result.answer)
            graded = grade_answer(item, result.status, result.answer, result.sources, relevant)
            results.append({
                "id": item.get("id"), "category": item.get("category"), "query": item["query"],
                "expected": item.get("expected"), "answer": result.answer, **graded,
                "relevant": bool(relevant) or bool(item.get("answer_keys")),
                "latencies": latencies, "answers_differ": len(set(answers)) > 1,
                "sources": [_source_payload(source) for source in result.sources],
                "manual": {"verdict": None, "note": None},
            })
    return results


def render(results: list[dict], summary: dict) -> str:
    lines = []
    for r in results:
        marks = [r["status"]]
        if r["key_hit"] is not None:
            marks.append(f"값={int(r['key_hit'])}/근거에있음={int(r['keys_in_sources'])}")
        if r["cited_relevant"] is not None:
            marks.append(f"인용={int(r['cited_relevant'])}/근거={int(r['given_relevant'])}")
        if r["refused"]:
            marks.append("거절")
        if r["leaks"]:
            marks.append(f"누출={r['leaks']}")
        if r["revision_cited"] is not None:
            marks.append(f"과거판인용={int(r['revision_cited'])}")
        latency = f"{max(r['latencies']):.1f}s" if r["latencies"] else "-"
        lines.append(f"{r['id']:<18} {latency:>7}  {' '.join(marks)}")
    lines.append("")
    lines.append("  ".join(
        f"{key}={value:.2f}" if isinstance(value, float) else f"{key}={value}"
        for key, value in summary.items()
    ))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("evalset", type=Path)
    parser.add_argument("--user", help="답변을 묻는 사용자 주체")
    parser.add_argument("--tag", action="append", help="평가 코퍼스의 태그 (반복 지정 가능)")
    parser.add_argument("--repeats", type=int, default=3, help="질문마다 생성을 되풀이할 횟수 (기본 3)")
    parser.add_argument("--out", type=Path, help="질문별 답·근거·채점과 요약을 JSON으로 저장할 경로")
    args = parser.parse_args()

    settings = get_settings()
    check_providers(settings.embedding_provider, settings.answer_provider)
    evalset_bytes = args.evalset.read_bytes()
    results = asyncio.run(evaluate(
        json.loads(evalset_bytes), settings.database_url,
        embedding_provider=get_provider(), answer_provider=get_answer_provider(),
        reset_cache=ollama_cache_reset(settings.ollama_url, settings.answer_model),
        user_id=args.user, tags=args.tag, context_chars=settings.answer_context_chars,
        repeats=args.repeats,
    ))
    summary = summarize(results)
    print(render(results, summary))
    if args.out:
        payload = {
            "model": settings.answer_model,
            "evalset": str(args.evalset),
            "evalset_sha256": hashlib.sha256(evalset_bytes).hexdigest(),
            "user": args.user, "tags": args.tag or [], "repeats": args.repeats,
            "context_chars": settings.answer_context_chars,
            "timeout_seconds": settings.answer_timeout_seconds,
            "summary": summary, "queries": results,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
