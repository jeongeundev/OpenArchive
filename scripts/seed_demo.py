#!/usr/bin/env python3
"""저장소 문서의 복사본을 관계 측정용 시연 데이터로 적재한다."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import get_settings
from app.services.documents import create_text_document

SEED_OWNER = "seed"
PRIVATE_TITLES = {
    "ADR-006:",
    "ADR-018:",
    "ADR-023:",
    "ADR-027:",
}
ADR_REFERENCE = re.compile(r"(?<!\[\[)\bADR-\d{3}\b")
BROKEN_LINK_TITLE = "ADR-999: 존재하지 않는 결정"


@dataclass(frozen=True)
class SeedDocument:
    title: str
    content: str
    tags: list[str]
    visibility: str = "public"


def split_sections(source: str, *, heading_level: int, heading_prefix: str = "") -> list[SeedDocument]:
    """지정한 Markdown 헤딩부터 다음 동급 헤딩 직전까지를 문서로 나눈다."""
    marks = "#" * heading_level
    pattern = re.compile(rf"(?m)^{re.escape(marks)} ({re.escape(heading_prefix)}[^\n]*)$")
    matches = list(pattern.finditer(source))
    documents: list[SeedDocument] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        content = source[match.start() : end].strip()
        if content.strip(" \t\r\n\f"):
            documents.append(SeedDocument(match.group(1).strip(), content, []))
    return documents


def _topic_tags(title: str, source_tag: str) -> list[str]:
    lowered = title.lower()
    topics = (
        ("검색", ("검색", "search", "hnsw", "vector", "관련 문서", "태그 추천")),
        ("openproxy", ("openproxy", "proxy", "접속", "listen", "failover")),
        ("임베딩", ("임베딩", "embedding", "worker", "워커", "청크")),
        ("권한", ("권한", "visibility", "private", "rls")),
        ("운영", ("배포", "설치", "운영", "복구", "ha", "장애")),
        ("설계", ("아키텍처", "설계", "결정", "구조")),
    )
    topic = next((tag for tag, words in topics if any(word in lowered for word in words)), "문서관리")
    return [source_tag, topic]


def _decorate(documents: list[SeedDocument], source_tag: str) -> list[SeedDocument]:
    return [
        SeedDocument(
            document.title,
            document.content,
            _topic_tags(document.title, source_tag),
            "private" if any(document.title.startswith(prefix) for prefix in PRIVATE_TITLES) else "public",
        )
        for document in documents
    ]


def _whole_file(path: Path, source_tag: str) -> SeedDocument:
    content = path.read_text()
    title = next(
        (line.removeprefix("# ").strip() for line in content.splitlines() if line.startswith("# ")),
        path.stem,
    )
    return SeedDocument(title, content, _topic_tags(title, source_tag))


def _add_wikilinks(documents: list[SeedDocument]) -> list[SeedDocument]:
    """ADR 번호 참조를 seed 문서의 전체 제목 위키링크로 바꾼다."""
    titles_by_number = {
        title.split(":", 1)[0]: title
        for title in (document.title for document in documents)
        if re.fullmatch(r"ADR-\d{3}:.*", title)
    }

    def replace_content(document: SeedDocument) -> SeedDocument:
        lines = []
        for line in document.content.splitlines():
            if line.startswith("### ADR-"):
                lines.append(line)
                continue
            lines.append(
                ADR_REFERENCE.sub(
                    lambda match: (
                        f"[[{titles_by_number[match.group(0)]}]]"
                        if match.group(0) in titles_by_number
                        else match.group(0)
                    ),
                    line,
                )
            )
        return SeedDocument(
            document.title,
            "\n".join(lines),
            document.tags,
            document.visibility,
        )

    linked = [replace_content(document) for document in documents]
    first = linked[0]
    linked[0] = SeedDocument(
        first.title,
        f"{first.content}\n\n시연용 깨진 링크: [[{BROKEN_LINK_TITLE}]]",
        first.tags,
        first.visibility,
    )
    return linked


def load_seed_documents(root: Path = ROOT) -> list[SeedDocument]:
    """저장소 원본은 수정하지 않고 적재할 복사본 목록만 만든다."""
    docs = root / "docs"
    result = _decorate(
        split_sections((docs / "ADR.md").read_text(), heading_level=3, heading_prefix="ADR-"),
        "adr",
    )
    result += _decorate(
        split_sections((docs / "OPENSQL_RESEARCH.md").read_text(), heading_level=2), "조사"
    )
    result += _decorate(
        split_sections((docs / "ARCHITECTURE.md").read_text(), heading_level=2), "아키텍처"
    )
    for filename, source_tag in (
        ("PRD.md", "prd"),
        ("UI_GUIDE.md", "ui"),
        ("PROJECT_CONTEXT.md", "요구사항"),
        ("SETUP_OPENSQL.md", "설치"),
    ):
        result.append(_whole_file(docs / filename, source_tag))
    result.append(_whole_file(root / "CLAUDE.md", "규칙"))
    return _add_wikilinks(result)


async def seed_documents(conn: psycopg.AsyncConnection, documents: list[SeedDocument]) -> int:
    """없는 seed 문서만 서비스 계층을 통해 적재하고 생성 건수를 반환한다."""
    existing = {
        row[0]
        for row in await (
            await conn.execute("SELECT title FROM documents WHERE owner_id = %s", (SEED_OWNER,))
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


async def run(reset: bool, timeout: float) -> None:
    documents = load_seed_documents()
    async with await psycopg.AsyncConnection.connect(
        get_settings().database_url, autocommit=True
    ) as conn:
        if reset:
            await conn.execute("DELETE FROM documents WHERE owner_id = %s", (SEED_OWNER,))
        created = await seed_documents(conn, documents)
        ready, elapsed = await wait_until_ready(conn, [document.title for document in documents], timeout)
        chunks = (
            await (
                await conn.execute(
                    """
                    SELECT count(*) FROM document_chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE d.owner_id = %s
                    """,
                    (SEED_OWNER,),
                )
            ).fetchone()
        )[0]
    print(f"seed 완료: 문서 {ready}개 (신규 {created}개), 청크 {chunks}개, {elapsed:.1f}초")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="owner_id=seed 문서만 삭제 후 재적재")
    parser.add_argument("--timeout", type=float, default=600, help="임베딩 완료 대기 시간(초)")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.reset, args.timeout))
    except (TimeoutError, RuntimeError, psycopg.Error) as exc:
        print(f"seed 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
