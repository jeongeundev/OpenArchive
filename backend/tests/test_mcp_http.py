import json
from contextlib import asynccontextmanager

import httpx
import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs
from starlette.applications import Starlette
from starlette.routing import Mount

from openarchive.config import get_settings
from openarchive.db import close_pool, get_pool
from openarchive.embeddings.fake import FakeProvider
from openarchive.mcp_server.http import RemoteMcp
from openarchive.services.auth import create_token, create_user, revoke_token


@pytest.fixture
async def database(monkeypatch, migrated_db):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    get_settings.cache_clear()
    await get_pool().open()
    try:
        yield migrated_db
    finally:
        await close_pool()


async def issue(dsn):
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        user = await create_user(conn, "kim", "test-password")
        token = await create_token(conn, user["id"], name="mcp")
    return user, token


@asynccontextmanager
async def client_for(remote, provider=None, *, running=True):
    @asynccontextmanager
    async def lifespan(app):
        if running:
            async with remote.running(provider or FakeProvider()):
                yield
        else:
            yield

    app = Starlette(routes=[Mount("/mcp", app=remote.asgi)], lifespan=lifespan)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


async def rpc(client, token, method, params=None, **headers):
    return await client.post(
        "/mcp/",
        headers={
            "Authorization": f"bEaReR {token}",
            "Accept": "application/json, text/event-stream",
            **headers,
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


async def tools(client, token, **headers):
    response = await rpc(
        client,
        token,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
        **headers,
    )
    assert response.status_code == 200, response.text
    response = await rpc(client, token, "tools/list", **headers)
    assert response.status_code == 200, response.text
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == {
        "search_documents",
        "get_document",
        "list_documents",
        "create_document",
    }


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic abc", "Bearer "])
async def test_invalid_credentials(database, authorization):
    async with client_for(RemoteMcp()) as client:
        headers = {} if authorization is None else {"Authorization": authorization}
        response = await client.post("/mcp/", headers=headers, json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_revoked_token(database):
    user, token = await issue(database)
    async with await psycopg.AsyncConnection.connect(database) as conn:
        await revoke_token(conn, token["id"], user_id=user["id"])
    async with client_for(RemoteMcp()) as client:
        response = await rpc(client, token["token"], "tools/list")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {
            "Host": "192.168.0.10:8000",
            "Origin": "http://192.168.0.10:8000",
        },
    ],
)
async def test_tools_and_remote_host(database, headers):
    _, token = await issue(database)
    async with client_for(RemoteMcp()) as client:
        await tools(client, token["token"], **headers)


async def test_repeated_lifespan(database):
    _, token = await issue(database)
    remote = RemoteMcp()
    for _ in range(2):
        async with client_for(remote) as client:
            await tools(client, token["token"])


async def test_outside_lifespan(database):
    _, token = await issue(database)
    async with client_for(RemoteMcp(), running=False) as client:
        response = await rpc(client, token["token"], "tools/list")
    assert response.status_code == 503


async def test_search_token_principal_and_injected_provider(database, monkeypatch):
    _, token = await issue(database)
    monkeypatch.setenv("MCP_USER_ID", "lee")
    get_settings.cache_clear()
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as conn:
        visible = await insert_test_document(conn, title="공개", content="OpenSQL 근거")
        hidden = await insert_test_document(
            conn, title="제한", content="OpenSQL 근거", owner_id="lee", visibility="private"
        )
        await process_all_embedding_jobs(conn, FakeProvider())

    class CountingProvider(FakeProvider):
        calls = 0

        def embed(self, texts):
            self.calls += 1
            return super().embed(texts)

    provider = CountingProvider()
    remote = RemoteMcp()
    async with client_for(remote, provider) as client:
        response = await rpc(
            client,
            token["token"],
            "tools/call",
            {
                "name": "search_documents",
                "arguments": {"query": "OpenSQL 근거"},
            },
        )
    assert response.status_code == 200
    result = response.json()["result"]
    assert not result.get("isError"), result
    payload = json.loads(result["content"][0]["text"])
    ids = {item["document_id"] for item in payload["items"]}
    assert str(visible) in ids
    assert str(hidden) not in ids
    assert provider.calls == 1
    # 요청이 끝난 뒤에도 원격 resolver는 stdio 환경으로 대체하지 않는다.
    with pytest.raises(RuntimeError):
        remote._resolve_principal()


async def last_used_at(dsn, token_id):
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        row = await (
            await conn.execute("SELECT last_used_at FROM api_tokens WHERE id = %s", (token_id,))
        ).fetchone()
    return row[0]


async def test_remote_request_records_last_use(database):
    _, token = await issue(database)
    assert await last_used_at(database, token["id"]) is None
    async with client_for(RemoteMcp()) as client:
        await tools(client, token["token"])
    assert await last_used_at(database, token["id"]) is not None


async def test_expired_token_is_401(database):
    _, token = await issue(database)
    async with await psycopg.AsyncConnection.connect(database) as conn:
        await conn.execute(
            "UPDATE api_tokens SET expires_at = now() - interval '1 minute' WHERE id = %s",
            (token["id"],),
        )
    async with client_for(RemoteMcp()) as client:
        response = await rpc(client, token["token"], "tools/list")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
