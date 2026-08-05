import psycopg
import pytest

from app.vectors import to_pgvector_literal


def test_literal_has_no_spaces_and_keeps_element_order():
    assert to_pgvector_literal([1.0, -0.5, 0.0]) == "[1.0,-0.5,0.0]"


async def test_pgvector_accepts_the_literal_including_scientific_notation(migrated_db: str):
    """작은 값은 파이썬이 1e-07처럼 지수 표기로 찍는다. pgvector가 그대로 받는지 확인한다.

    받지 못하면 임베딩 삽입이 통째로 실패하므로, 포맷 문자열만 보는 것으로는 부족하다.
    """
    vector = [1e-07, -2.5, 0.0, 3.4e10]

    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        cur = await conn.execute("SELECT %s::vector", (to_pgvector_literal(vector),))
        stored = (await cur.fetchone())[0]

    # pgvector는 float4로 저장하므로 float64 입력과 완전히 같지는 않다.
    assert [float(value) for value in stored.strip("[]").split(",")] == pytest.approx(
        vector, rel=1e-6
    )
