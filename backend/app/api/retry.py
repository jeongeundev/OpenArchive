"""연결이 끊긴 요청을 한 번 다시 태우는 미들웨어.

`ARCHITECTURE.md`의 "애플리케이션이 담당하는 복구 로직"에서 API 몫에 해당한다. 풀의
`check=check_connection`은 **대여 시점**의 죽은 연결만 걸러내므로, 처리 도중 끊긴
경우는 남는다. 재시도가 미들웨어에 있는 이유는 핸들러에 이미 주입된 연결을 다시 써봐야
소용이 없기 때문이다 — 요청 전체를 다시 태워야 의존성이 새로 풀리고 풀에서 새 연결을 빌린다.

**쓰기는 재시도하지 않는다.** COMMIT이 서버에 닿은 뒤 응답만 잃은 경우와 아예 닿지
못한 경우를 구분할 수 없어, 재시도하면 문서가 두 번 생길 수 있다. 문서의 "요청 핸들러는
1회 재시도"보다 좁은 범위다.

`BaseHTTPMiddleware`를 쓰지 않는다 — 그쪽 `call_next`는 두 번 호출하면 앱은 다시 돌지만
첫 시도에 저장해 둔 예외를 그대로 다시 던진다(Starlette 1.3.1 실측). 재시도하려면
요청 본문 재생과 응답 송신 시점을 직접 통제해야 한다.
"""

from collections.abc import Awaitable, Callable

import psycopg

Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


def is_retryable(scope: dict) -> bool:
    """재시도해도 중복 쓰기가 없는 읽기 전용 요청인지 판별한다.

    POST /api/search는 메서드만 POST인 읽기라 포함한다 — 페일오버 중에도 검색이
    계속 성공해야 한다는 것이 이 재시도의 목적이다.
    """
    return scope["method"] in ("GET", "HEAD") or scope["path"] == "/api/search"


async def _buffer_body(receive: Receive) -> bytes:
    """본문을 모두 읽어 둔다. 두 번째 시도에 다시 흘려보내야 하기 때문이다.

    재시도 대상은 읽기 요청뿐이라 본문이 작다. 업로드는 이 경로로 오지 않는다.
    """
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replay(body: bytes) -> Receive:
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


class RetryOnOperationalError:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_retryable(scope):
            await self.app(scope, receive, send)
            return

        body = await _buffer_body(receive)
        responded = False

        async def tracking_send(message: Message) -> None:
            nonlocal responded
            responded = True
            await send(message)

        try:
            await self.app(scope, _replay(body), tracking_send)
        except psycopg.OperationalError:
            # 응답이 이미 나가기 시작했으면 되돌릴 수 없다.
            if responded:
                raise
            await self.app(scope, _replay(body), send)
