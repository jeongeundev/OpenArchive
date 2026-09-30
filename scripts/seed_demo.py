#!/usr/bin/env python3
"""시연·측정용으로 예제 코퍼스(`backend/app/demo_corpus/`)를 적재한다.

적재 로직은 `openarchive demo`와 같다(`app.demo`). 이 스크립트가 더하는 것은 계정이 없어도
되는 측정용 소유자(`seed`)와 `--reset`뿐이다 — 설치한 사람이 쓰는 입구는 `openarchive demo`다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import get_settings
from app.demo import converge, load_seed_documents, seed_documents

SEED_OWNER = "seed"


async def run(reset: bool, timeout: float, owner: str) -> None:
    async with await psycopg.AsyncConnection.connect(
        get_settings().database_url, autocommit=True
    ) as conn:
        if reset:
            await conn.execute(
                "DELETE FROM documents WHERE owner_id = %s", (owner,)
            )
        documents = load_seed_documents()
        created = await seed_documents(conn, documents, owner)
        result = await converge(
            conn,
            documents,
            owner,
            timeout=timeout,
            on_progress=lambda done, total: print(f"관계 재계산 {done}/{total}"),
        )
    print(
        f"seed 완료: 문서 {result.ready}개 (신규 {created}개), 청크 {result.chunks}개, "
        f"관계 {result.edge_pairs}쌍, 관계 재계산 {result.rebuilt}건, {result.elapsed:.1f}초"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--owner",
        default=SEED_OWNER,
        help=(
            "적재 문서의 소유자. 비공개 문서는 소유자에게만 보이므로(ADR-018) "
            "시연에서는 로그인 계정과 같게 준다. --reset도 이 소유자에만 적용된다 — "
            "로그인 계정으로 주면 그 계정이 업로드한 문서까지 지워진다."
        ),
    )
    parser.add_argument(
        "--reset", action="store_true", help="같은 소유자의 문서만 삭제 후 재적재"
    )
    parser.add_argument(
        "--timeout", type=float, default=600, help="임베딩 완료 대기 시간(초)"
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args.reset, args.timeout, args.owner))
    except (ValueError, TimeoutError, RuntimeError, psycopg.Error) as exc:
        print(f"seed 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
