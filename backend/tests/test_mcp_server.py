from uuid import uuid4

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from app.config import get_settings
from app.db import close_pool, get_pool
from app.embeddings import FakeProvider
from app.services.search import search_documents as search_documents_service


@pytest.fixture
async def mcp_database(monkeypatch, migrated_db: str):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.delenv("MCP_USER_ID", raising=False)
    get_settings.cache_clear()
    pool = get_pool()
    await pool.open()
    try:
        yield migrated_db
    finally:
        await close_pool()


async def _seed_documents(dsn: str):
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        public_id = await insert_test_document(
            conn,
            title="공개 근거",
            content="OpenSQL 공개 정합성 근거",
            tags=["공개"],
        )
        await conn.execute(
            "UPDATE documents SET filename = %s WHERE id = %s", ("public.md", public_id)
        )
        private_id = await insert_test_document(
            conn,
            title="비공개 근거",
            content="OpenSQL 비공개 정합성 근거",
            owner_id="alice",
            visibility="private",
            tags=["비공개"],
        )
        await process_all_embedding_jobs(conn, FakeProvider())
    return public_id, private_id


async def test_registers_exactly_three_evidence_tools():
    from mcp_server.server import mcp

    tools = await mcp.list_tools()

    assert {tool.name for tool in tools} == {
        "search_documents",
        "get_document",
        "list_documents",
    }


async def test_search_tool_matches_shared_service_and_returns_evidence(mcp_database):
    from mcp_server.server import search_documents

    await _seed_documents(mcp_database)

    tool_result = await search_documents("OpenSQL 공개 정합성")
    async with get_pool().connection() as conn:
        service_result = await search_documents_service(
            conn, FakeProvider(), query="OpenSQL 공개 정합성"
        )

    assert [item["document_id"] for item in tool_result["items"]] == [
        str(hit.document_id) for hit in service_result
    ]
    assert tool_result["items"][0]["excerpt"]
    assert tool_result["items"][0]["title"]
    assert tool_result["items"][0]["based_on_version"] == 1


async def test_mcp_user_setting_controls_private_access_for_all_tools(
    monkeypatch, mcp_database
):
    from app.services.documents import DocumentNotFound
    from mcp_server.server import get_document, list_documents, search_documents

    public_id, private_id = await _seed_documents(mcp_database)

    anonymous_search = await search_documents("OpenSQL 정합성")
    anonymous_list = await list_documents()
    with pytest.raises(DocumentNotFound):
        await get_document(str(private_id))

    assert {item["document_id"] for item in anonymous_search["items"]} == {str(public_id)}
    assert {item["document_id"] for item in anonymous_list["items"]} == {str(public_id)}

    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()

    owner_search = await search_documents("OpenSQL 정합성")
    owner_list = await list_documents()
    owner_detail = await get_document(str(private_id))

    assert str(private_id) in {item["document_id"] for item in owner_search["items"]}
    assert str(private_id) in {item["document_id"] for item in owner_list["items"]}
    assert owner_detail["document_id"] == str(private_id)
    assert owner_detail["content"] == "OpenSQL 비공개 정합성 근거"
    assert owner_detail["versions"]
    assert owner_detail["chunk_count"] == 1
    assert owner_detail["chunk_version"] == 1


async def test_get_document_rejects_missing_document(mcp_database):
    from app.services.documents import DocumentNotFound
    from mcp_server.server import get_document

    with pytest.raises(DocumentNotFound):
        await get_document(str(uuid4()))
