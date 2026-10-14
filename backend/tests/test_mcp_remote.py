# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""제출 명세의 원격 MCP 계약을 실제 SDK 클라이언트로 검증한다."""

import json
from contextlib import asynccontextmanager

import httpx
import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs, running_app
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from openarchive.config import get_settings
from openarchive.embeddings.fake import FakeProvider
from openarchive.main import app
from openarchive.services.auth import create_token, create_user, revoke_token
from openarchive.services.shares import add_document, create_share, issue_share_token


@pytest.fixture
async def remote(monkeypatch, migrated_db):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.delenv("MCP_USER_ID", raising=False)
    get_settings.cache_clear()
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        users = {name: await create_user(conn, name, "test-password") for name in ("kim", "lee")}
        tokens = {}
        for name, scope in (("kim", "read"), ("lee", "read"), ("write", "read_write")):
            tokens[name] = await create_token(
                conn, users["kim" if name == "write" else name]["id"], name=name, scope=scope
            )
        ids = {}
        for name, owner, visibility in (
            ("hidden", "lee", "private"),
            ("shared", "lee", "public"),
            ("excluded", "kim", "public"),
            ("kim", "kim", "private"),
        ):
            ids[name] = await insert_test_document(
                conn,
                title=name,
                content="OpenSQL 정합성 근거",
                owner_id=owner,
                visibility=visibility,
            )
        share = await create_share(conn, owner="lee", name="협업")
        await add_document(conn, share["id"], ids["shared"], owner="lee")
        tokens["share"] = await issue_share_token(conn, share["id"], owner="lee", name="mcp")
        await process_all_embedding_jobs(conn, FakeProvider())
    async with running_app(app):
        yield migrated_db, tokens, ids


@asynccontextmanager
async def session(token, responses=None):
    async def record(response):
        if responses is not None:
            responses.append(response)

    def factory(**kwargs):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), event_hooks={"response": [record]}, **kwargs
        )

    async with (
        streamablehttp_client(
            "http://test/mcp",
            headers={"Authorization": f"Bearer {token}"},
            httpx_client_factory=factory,
        ) as (read, write, _),
        ClientSession(read, write) as client,
    ):
        await client.initialize()
        yield client


def payload(result):
    assert not result.isError, result
    return json.loads(result.content[0].text)


async def count_title(dsn, title):
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cur = await conn.execute("SELECT count(*) FROM documents WHERE title = %s", (title,))
        return (await cur.fetchone())[0]


async def test_connect_tools_and_exact_path(remote):
    _, tokens, _ = remote
    responses = []
    async with session(tokens["kim"]["token"], responses) as client:
        assert {t.name for t in (await client.list_tools()).tools} == {
            "search_documents",
            "get_document",
            "list_documents",
            "create_document",
        }
        assert payload(await client.call_tool("search_documents", {"query": "근거"}))["items"]
    posts = [r for r in responses if r.request.method == "POST"]
    assert any(r.status_code == 200 for r in posts)
    assert all(r.request.url.path == "/mcp" and r.status_code not in (307, 308) for r in posts)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        response = await client.get(
            "http://test/mcp", headers={"Authorization": f"Bearer {tokens['kim']['token']}"}
        )
        assert response.status_code not in (307, 308)
        assert "text/html" not in response.headers.get("content-type", "")


async def test_missing_and_revoked_token(remote):
    dsn, tokens, _ = remote
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cur = await conn.execute("SELECT id FROM users WHERE username = 'kim'")
        await revoke_token(conn, tokens["kim"]["id"], user_id=(await cur.fetchone())[0])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        for headers in ({}, {"Authorization": f"Bearer {tokens['kim']['token']}"}):
            response = await client.post("http://test/mcp", headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"


async def test_token_visibility_overrides_stdio_environment(remote, monkeypatch):
    _, tokens, ids = remote
    monkeypatch.setenv("MCP_USER_ID", "lee")
    get_settings.cache_clear()
    async with session(tokens["kim"]["token"]) as client:
        for tool, arguments in (
            ("search_documents", {"query": "hidden"}),
            ("list_documents", {}),
        ):
            items = payload(await client.call_tool(tool, arguments))["items"]
            assert str(ids["hidden"]) not in {item["document_id"] for item in items}
        assert (await client.call_tool("get_document", {"document_id": str(ids["hidden"])})).isError
    async with session(tokens["lee"]["token"]) as client:
        items = payload(await client.call_tool("search_documents", {"query": "hidden"}))["items"]
        assert str(ids["hidden"]) in {item["document_id"] for item in items}


async def test_read_token_cannot_create(remote):
    dsn, tokens, _ = remote
    async with session(tokens["kim"]["token"]) as client:
        result = await client.call_tool(
            "create_document", {"title": "read 거부", "content": "근거"}
        )
        assert result.isError
        assert "쓰기 권한이 필요합니다" in result.content[0].text
    assert await count_title(dsn, "read 거부") == 0


async def test_past_version_follows_the_token_owner_over_http(remote):
    _, tokens, ids = remote
    async with session(tokens["kim"]["token"]) as client:
        own = payload(
            await client.call_tool("get_document", {"document_id": str(ids["kim"]), "version": 1})
        )
    async with session(tokens["lee"]["token"]) as client:
        other = await client.call_tool(
            "get_document", {"document_id": str(ids["kim"]), "version": 1}
        )
        missing = await client.call_tool(
            "get_document", {"document_id": "00000000-0000-0000-0000-000000000001", "version": 1}
        )
    assert own["document_id"] == str(ids["kim"]) and own["version"] == 1
    assert own["content"] == "OpenSQL 정합성 근거"
    # 볼 수 없는 문서의 판은 없는 문서의 판과 같은 오류여야 존재가 새지 않는다.
    assert other.isError and missing.isError
    assert other.content[0].text == missing.content[0].text


async def test_unknown_arguments_are_rejected_not_ignored(remote):
    """범위를 좁히려던 인자를 잘못 쓰면 거부된다 — 무시하면 조용히 넓은 범위로 검색·저장한다."""
    dsn, tokens, _ = remote
    async with session(tokens["write"]["token"]) as client:
        searched = await client.call_tool(
            "search_documents", {"query": "근거", "folder": "00000000-0000-0000-0000-000000000000"}
        )
        created = await client.call_tool(
            "create_document", {"title": "인자 거부", "content": "근거", "owner": "lee"}
        )
    assert searched.isError
    # folder_id의 오타다. 받는 인자 목록에도 "folder"가 들어 있으니 거부한 인자 자리를 본다.
    assert "없는 인자입니다: folder (" in searched.content[0].text
    assert created.isError
    assert "없는 인자입니다: owner (" in created.content[0].text
    assert await count_title(dsn, "인자 거부") == 0


async def test_write_token_owner_rest_list_and_audit(remote):
    dsn, tokens, _ = remote
    async with session(tokens["write"]["token"]) as client:
        created = payload(
            await client.call_tool("create_document", {"title": "원격 생성", "content": "근거"})
        )
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cur = await conn.execute(
            "SELECT owner_id FROM documents WHERE id = %s", (created["document_id"],)
        )
        assert (await cur.fetchone())[0] == "kim"
        cur = await conn.execute(
            "SELECT actor, actor_via FROM audit_log WHERE document_id = %s AND action = 'document_created'",
            (created["document_id"],),
        )
        assert await cur.fetchall() == [("kim", "mcp")]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        response = await client.get(
            "http://test/api/documents",
            headers={
                "Authorization": f"Bearer {tokens['write']['token']}",
            },
        )
        assert response.status_code == 200
        assert created["document_id"] in {item["id"] for item in response.json()}


async def test_share_token_reads_only_included_document_and_cannot_create(remote):
    dsn, tokens, ids = remote
    async with session(tokens["share"]["token"]) as client:
        for tool, arguments in (("search_documents", {"query": "근거"}), ("list_documents", {})):
            items = payload(await client.call_tool(tool, arguments))["items"]
            assert {item["document_id"] for item in items} == {str(ids["shared"])}
        assert payload(
            await client.call_tool(
                "get_document",
                {
                    "document_id": str(ids["shared"]),
                },
            )
        )["document_id"] == str(ids["shared"])
        assert (
            await client.call_tool("get_document", {"document_id": str(ids["excluded"])})
        ).isError
        result = await client.call_tool(
            "create_document", {"title": "공유 거부", "content": "근거"}
        )
        assert result.isError
        assert "쓰기 권한이 필요합니다" in result.content[0].text
    assert await count_title(dsn, "공유 거부") == 0
