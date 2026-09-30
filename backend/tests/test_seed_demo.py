"""`scripts/seed_demo.py` — 측정·시연용 적재 스크립트. 적재 로직은 app.demo와 같다.

스크립트가 더하는 것은 소유자 고정(`seed`)과 `--reset`뿐이다. 코퍼스·적재 자체는 test_demo.py.
"""

import sys
from pathlib import Path

import psycopg
from conftest import process_all_embedding_jobs

from app.config import get_settings
from app.demo import load_seed_documents, seed_documents
from app.embeddings.fake import FakeProvider

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.seed_demo import run


async def test_seed_run_rebuilds_edges_after_embedding(migrated_db: str, monkeypatch, capsys):
    documents = load_seed_documents()
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        assert await seed_documents(conn, documents) == len(documents)
        assert await process_all_embedding_jobs(conn, FakeProvider()) == len(documents) * 2
        first_id, = await (
            await conn.execute(
                "SELECT id FROM documents WHERE owner_id = 'seed' ORDER BY created_at, id LIMIT 1"
            )
        ).fetchone()
        query = "SELECT count(*) FROM document_edges WHERE src_document_id = %s"
        # 017 이후 관계 판정은 별도 잡이라 임베딩 잡이 전부 끝난 뒤에 돈다 — 일괄 적재에서는
        # 첫 문서도 나중 문서를 이웃 후보로 보므로, 예전처럼 "첫 문서의 관계가 비어 있는
        # 상태"가 저절로 생기지 않는다(상시 워커에서는 여전히 생긴다 — ADR-029 결정 6).
        # 재계산이 실제로 판정을 다시 돌리는지 보려면 그 자리를 직접 비운다.
        seeded, = await (await conn.execute(query, (first_id,))).fetchone()
        assert seeded > 0
        await conn.execute(
            "DELETE FROM document_edges WHERE src_document_id = %s", (first_id,)
        )
        assert await (await conn.execute(query, (first_id,))).fetchone() == (0,)

        monkeypatch.setenv("DATABASE_URL", migrated_db)
        get_settings.cache_clear()
        await run(reset=False, timeout=10, owner="seed")

        count, = await (await conn.execute(query, (first_id,))).fetchone()
        assert count > 0
        output = capsys.readouterr().out
        assert "신규 0개" in output
        assert f"관계 재계산 {len(documents)}건" in output
