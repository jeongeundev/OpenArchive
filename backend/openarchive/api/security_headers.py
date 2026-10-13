# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""앱 전체 응답에 보안 헤더를 붙이는 미들웨어.

정적 프론트·API·원격 MCP·원본 파일이 한 오리진에서 나가므로(ADR-041) 한 곳에서 붙인다.
응답이 이미 정한 헤더는 덮지 않는다 — 원본 미리보기의 `Content-Security-Policy: sandbox`와
`nosniff`가 그 예다.

- `X-Frame-Options: DENY` — 앱은 자기 화면을 프레임에 넣지 않는다(미리보기도 새 탭).
  같은 오리진 프레임이 생기면 그때 `SAMEORIGIN`으로 바꾼다.
- `Referrer-Policy: same-origin` — 검색어가 실린 URL을 바깥 사이트로 흘리지 않는다.
- 전체 CSP는 넣지 않는다. Next 정적 산출물의 인라인 스크립트를 깨뜨린다.

`BaseHTTPMiddleware` 대신 순수 ASGI로 쓴다 — 스트리밍 응답(원격 MCP)을 감싸지 않고
응답 시작 메시지의 헤더만 고친다.
"""

from collections.abc import Awaitable, Callable

SECURITY_HEADERS = (
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
)

Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[dict, Receive, Send], Awaitable[None]]


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers += [
                    (name, value) for name, value in SECURITY_HEADERS if name not in present
                ]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
