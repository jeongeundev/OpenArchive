#!/usr/bin/env python3
"""시연·측정용 문서 코퍼스(`scripts/demo_corpus/`)를 적재한다.

코퍼스는 가상 회사의 사내 문서다. 저장소 자체 문서를 쪼개 넣던 이전 방식은 전부 한
주제여서 관계 그래프가 거의 완전그래프가 됐고(문서당 평균 19.6개 관계), Louvain
덩어리가 출처 태그로만 갈려 화면에서 읽을 것이 없었다. 부서가 다섯이면 청크의 최근접
이웃이 부서 안에 모이고, 부서를 가로지르는 위키링크가 그 사이를 잇는다.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import get_settings
from app.services.documents import create_text_document

SEED_OWNER = "seed"
CORPUS_ROOT = ROOT / "scripts" / "demo_corpus"
FRONT_MATTER_KEYS = frozenset({"title", "tags", "visibility"})
VISIBILITIES = ("public", "private")
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class SeedDocument:
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    visibility: str = "public"


def parse_seed_document(text: str) -> SeedDocument:
    """코퍼스 파일 한 개를 적재할 문서로 바꾼다.

    프런트매터의 `title`은 선택이며, 없으면 본문 첫 `# ` 헤딩을 제목으로 쓴다. 본문이
    같은 문서를 서로 다른 제목으로 넣을 때만 `title`을 적는다 — 제목이 겹치면
    `seed_documents`가 중복으로 보고 조용히 건너뛴다.
    """
    match = FRONT_MATTER.match(text)
    if match is None:
        raise ValueError("파일이 '---' 프런트매터 블록으로 시작하지 않는다")

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"프런트매터 항목이 'key: value' 형식이 아니다: {line!r}")
        meta[key.strip()] = value.strip()

    unknown = set(meta) - FRONT_MATTER_KEYS
    if unknown:
        raise ValueError(f"모르는 프런트매터 항목: {sorted(unknown)}")

    tags = [tag.strip() for tag in meta.get("tags", "").split(",") if tag.strip()]
    if not tags:
        raise ValueError("tags에 최소 한 개가 있어야 한다")

    visibility = meta.get("visibility", "public")
    if visibility not in VISIBILITIES:
        raise ValueError(f"visibility는 {VISIBILITIES} 중 하나여야 한다: {visibility!r}")

    content = text[match.end() :].strip()
    if not content:
        raise ValueError("본문이 비어 있다")

    return SeedDocument(
        title=meta.get("title") or _heading_title(content),
        content=content,
        tags=tags,
        visibility=visibility,
    )


def _heading_title(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    raise ValueError("본문에 '# 제목' 헤딩이 없고 프런트매터에도 title이 없다")


def load_seed_documents(root: Path = CORPUS_ROOT) -> list[SeedDocument]:
    """코퍼스 디렉토리의 Markdown을 경로 순서대로 읽는다."""
    paths = sorted(root.rglob("*.md"))
    if not paths:
        raise ValueError(f"코퍼스 파일이 없다: {root}")

    documents = []
    for path in paths:
        try:
            documents.append(parse_seed_document(path.read_text()))
        except ValueError as exc:
            raise ValueError(f"{path.relative_to(root)}: {exc}") from exc
    return documents


async def seed_documents(
    conn: psycopg.AsyncConnection, documents: list[SeedDocument]
) -> int:
    """없는 seed 문서만 서비스 계층을 통해 적재하고 생성 건수를 반환한다."""
    existing = {
        row[0]
        for row in await (
            await conn.execute(
                "SELECT title FROM documents WHERE owner_id = %s", (SEED_OWNER,)
            )
        ).fetchall()
    }
    created = 0
    for document in documents:
        if document.title in existing:
            continue
        await create_text_document(
            conn,
            title=document.title,
            content=document.content,
            content_type="md",
            owner_id=SEED_OWNER,
            tags=document.tags,
            visibility=document.visibility,
        )
        existing.add(document.title)
        created += 1
    return created


async def wait_until_ready(
    conn: psycopg.AsyncConnection, titles: list[str], timeout: float
) -> tuple[int, float]:
    started = time.monotonic()
    expected = len(titles)
    while time.monotonic() - started < timeout:
        ready, failed = (
            await (
                await conn.execute(
                    """
                    SELECT count(*) FILTER (WHERE embedding_status = 'ready'),
                           count(*) FILTER (WHERE embedding_status = 'error')
                    FROM documents WHERE owner_id = %s AND title = ANY(%s)
                    """,
                    (SEED_OWNER, titles),
                )
            ).fetchone()
        )
        if failed:
            raise RuntimeError(f"임베딩에 실패한 seed 문서가 {failed}개 있습니다.")
        if ready == expected:
            return ready, time.monotonic() - started
        await asyncio.sleep(1)
    raise TimeoutError("임베딩 대기 시간이 초과되었습니다. 워커가 떠 있는지 확인하세요.")


async def summarize(conn: psycopg.AsyncConnection) -> tuple[int, int]:
    """적재 결과로 만들어진 청크 수와 관계 문서쌍 수를 센다.

    관계는 트리거가 양방향 두 행으로 저장하므로(ADR-029) 문서쌍으로 접어 센다.
    """
    return await (
        await conn.execute(
            """
            SELECT (SELECT count(*) FROM document_chunks c
                      JOIN documents d ON d.id = c.document_id
                     WHERE d.owner_id = %s),
                   (SELECT count(*) FROM (
                        SELECT DISTINCT least(src_document_id, dst_document_id),
                                        greatest(src_document_id, dst_document_id)
                        FROM document_edges) pairs)
            """,
            (SEED_OWNER,),
        )
    ).fetchone()


async def run(reset: bool, timeout: float) -> None:
    documents = load_seed_documents()
    async with await psycopg.AsyncConnection.connect(
        get_settings().database_url, autocommit=True
    ) as conn:
        if reset:
            await conn.execute(
                "DELETE FROM documents WHERE owner_id = %s", (SEED_OWNER,)
            )
        created = await seed_documents(conn, documents)
        ready, elapsed = await wait_until_ready(
            conn, [document.title for document in documents], timeout
        )
        chunks, edge_pairs = await summarize(conn)
    print(
        f"seed 완료: 문서 {ready}개 (신규 {created}개), 청크 {chunks}개, "
        f"관계 {edge_pairs}쌍, {elapsed:.1f}초"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset", action="store_true", help="owner_id=seed 문서만 삭제 후 재적재"
    )
    parser.add_argument(
        "--timeout", type=float, default=600, help="임베딩 완료 대기 시간(초)"
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args.reset, args.timeout))
    except (ValueError, TimeoutError, RuntimeError, psycopg.Error) as exc:
        print(f"seed 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
