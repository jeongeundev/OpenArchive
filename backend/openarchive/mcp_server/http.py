"""Bearer API 토큰으로 인증하는 원격 Streamable HTTP MCP (ADR-056)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from openarchive.api.deps import BEARER_PREFIX
from openarchive.db import connection
from openarchive.embeddings.base import EmbeddingProvider
from openarchive.mcp_server.server import McpPrincipal, build_server, principal_from_token
from openarchive.services.auth import AuthenticationFailed, validate_token


class RemoteMcp:
    """API 앱에 붙는 원격 MCP. 주체는 Bearer 토큰에서만 정한다 (ADR-056)."""

    def __init__(self) -> None:
        self._principal: ContextVar[McpPrincipal | None] = ContextVar(
            "remote_mcp_principal", default=None
        )
        self._provider: EmbeddingProvider | None = None
        self._manager: StreamableHTTPSessionManager | None = None
        self._server = build_server(
            self._resolve_principal,
            self._resolve_provider,
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    def _resolve_principal(self) -> McpPrincipal:
        principal = self._principal.get()
        if principal is None:
            raise RuntimeError("원격 MCP 토큰 주체가 없습니다.")
        return principal

    def _resolve_provider(self) -> EmbeddingProvider:
        if self._provider is None:
            raise RuntimeError("원격 MCP가 실행 중이 아닙니다.")
        return self._provider

    @asynccontextmanager
    async def running(self, provider: EmbeddingProvider) -> AsyncIterator[None]:
        # SDK의 캐시된 매니저는 run()을 한 번만 허용한다. 공개 팩토리가 없어
        # 저수준 서버 접근은 여기 한 곳에 두고 lifespan마다 매니저를 새로 만든다.
        manager = StreamableHTTPSessionManager(
            app=self._server._mcp_server,
            json_response=True,
            stateless=True,
            security_settings=self._server.settings.transport_security,
        )
        async with manager.run():
            self._provider = provider
            self._manager = manager
            try:
                yield
            finally:
                self._manager = None
                self._provider = None

    async def asgi(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        authorization = Headers(scope=scope).get("authorization", "")
        if not authorization.lower().startswith(BEARER_PREFIX.lower()):
            await self._unauthorized(scope, receive, send)
            return
        try:
            async with connection() as conn:
                user = await validate_token(conn, authorization[len(BEARER_PREFIX) :])
        except AuthenticationFailed:
            await self._unauthorized(scope, receive, send)
            return
        manager = self._manager
        if manager is None:
            await JSONResponse({"detail": "원격 MCP가 실행 중이 아닙니다."}, status_code=503)(
                scope, receive, send
            )
            return
        token = self._principal.set(principal_from_token(user))
        try:
            # stateless 매니저가 시작하는 서버 태스크도 이 요청의 context를 상속한다.
            await manager.handle_request(scope, receive, send)
        finally:
            self._principal.reset(token)

    async def _unauthorized(self, scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse(
            {"detail": "API 토큰이 필요합니다."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )(scope, receive, send)


remote_mcp = RemoteMcp()
