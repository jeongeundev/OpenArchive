"""사용자 CLI의 REST 클라이언트 — 본인 API 토큰으로만 서버에 붙는다 (#189, ADR-057).

DB에 붙지 않고 `DATABASE_URL`·`.env`를 읽지 않는다. DSN을 쥔 클라이언트는 열람 범위 밖에
있어 남으로 행세할 수 있기 때문이다. 열람 범위·소유자·쓰기 권한·낙관적 잠금은 서버가 웹과
똑같이 판정하고, 여기서는 그 판정을 사람이 읽을 문구로 옮기기만 한다.

재시도는 웹 UI(`frontend/src/lib/api.ts`)와 기준이 같다(ADR-048 결정 4): 읽기와 멱등키가
붙은 업로드만 503·연결 오류에 지수 백오프 + 전체 지터로 다시 보낸다. 다른 쓰기는 첫 시도가
이미 커밋됐을 수 있어 다시 보내면 두 번 실행된다.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from openarchive.config import openarchive_home

# 테스트가 앱의 트랜스포트를 꽂는 자리 하나 (D9). None이면 실제 네트워크로 간다.
TRANSPORT: httpx.BaseTransport | None = None

BACKOFF_START_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 8.0
BACKOFF_BUDGET_SECONDS = 60.0

# 메서드만 POST인 읽기, 멱등키가 있으면 다시 보내도 되는 생성 — 웹 UI와 같은 목록이다.
READ_POST_PATHS = ("/api/search", "/api/ask")
IDEMPOTENT_CREATE_PATHS = ("/api/documents",)

LOGIN_AGAIN = "토큰이 올바르지 않습니다. openarchive login으로 다시 로그인하세요."
UNAVAILABLE = "서버가 일시적으로 응답하지 못했습니다. 잠시 후 다시 실행하세요."
NO_RESPONSE = (
    "서버에서 응답을 받지 못했습니다: {url} — 요청이 반영됐을 수 있으니 "
    "openarchive doc show 등으로 반영됐는지 확인한 뒤 다시 실행하세요."
)
RETRYING = "서버가 일시적으로 응답하지 않아 다시 시도하는 중입니다…"


@dataclass(frozen=True)
class Credentials:
    url: str
    token: str


def credentials_path() -> Path:
    return openarchive_home() / "credentials.json"


def load_credentials() -> Credentials | None:
    path = credentials_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Credentials(url=data["url"], token=data["token"])
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError):
        return None


def save_credentials(credentials: Credentials) -> None:
    """본인만 읽는 권한(600)으로 쓴다. 디렉터리는 없을 때만 700으로 만든다 (D4)."""
    path = credentials_path()
    if not path.parent.exists():
        path.parent.mkdir(parents=True, mode=0o700)
    payload = json.dumps({"url": credentials.url, "token": credentials.token})
    # 처음부터 600으로 연다 — 쓴 뒤에 chmod하면 그 사이 남이 읽을 수 있다.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(payload)
    # 이미 있던 파일은 O_CREAT의 모드가 적용되지 않는다.
    path.chmod(0o600)


class ApiError(Exception):
    """사용자에게 그대로 보일 메시지와 HTTP 상태(연결 실패면 None), 응답 본문."""

    def __init__(self, message: str, *, status: int | None, body: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body


class NotLoggedIn(Exception):
    pass


def is_retryable(method: str, path: str, headers: dict[str, str] | None) -> bool:
    method = method.upper()
    if method in ("GET", "HEAD") or (method == "POST" and path in READ_POST_PATHS):
        return True
    has_key = any(name.lower() == "idempotency-key" for name in (headers or {}))
    return method == "POST" and path in IDEMPOTENT_CREATE_PATHS and has_key


def retry_after_seconds(response: httpx.Response) -> float:
    """`Retry-After`(초 또는 HTTP 날짜)를 초로. 없거나 읽을 수 없으면 0."""
    value = response.headers.get("Retry-After")
    if value is None:
        return 0.0
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, when.timestamp() - time.time())


def _body(response: httpx.Response) -> dict | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _error_for(response: httpx.Response) -> ApiError:
    body = _body(response)
    status = response.status_code
    if status == 401:
        return ApiError(LOGIN_AGAIN, status=status, body=body)
    if status == 503:
        return ApiError(UNAVAILABLE, status=status, body=body)
    detail = (body or {}).get("detail")
    if isinstance(detail, str):
        message = detail
    elif isinstance(detail, list):
        parts = [str(item.get("msg")) for item in detail if isinstance(item, dict)]
        message = "요청 값이 올바르지 않습니다: " + "; ".join(parts)
    else:
        message = f"요청에 실패했습니다. ({status})"
    return ApiError(message, status=status, body=body)


class ApiClient:
    def __init__(
        self,
        credentials: Credentials,
        *,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        self.credentials = credentials
        self._sleep = sleep
        self._timeout = timeout

    @classmethod
    def from_saved(cls) -> ApiClient:
        credentials = load_credentials()
        if credentials is None:
            raise NotLoggedIn
        return cls(credentials)

    def request(
        self,
        method: str,
        path: str,
        *,
        json=None,
        params=None,
        files=None,
        data=None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """2xx면 응답을, 아니면 ApiError. 재시도 여부는 메서드·경로·멱등키가 정한다 (D3)."""
        retryable = is_retryable(method, path, headers)
        all_headers = {"Authorization": f"Bearer {self.credentials.token}", **(headers or {})}
        waited = 0.0
        attempt = 0
        notified = False
        with httpx.Client(
            base_url=self.credentials.url,
            transport=TRANSPORT,
            timeout=self._timeout if timeout is None else timeout,
        ) as http:
            while True:
                failure: ApiError
                retry_after = 0.0
                try:
                    response = http.request(
                        method,
                        path,
                        json=json,
                        params=params,
                        files=files,
                        data=data,
                        headers=all_headers,
                    )
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    failure = ApiError(
                        f"서버에 연결하지 못했습니다: {self.credentials.url}", status=None
                    )
                except httpx.TransportError:
                    # 요청이 서버에 닿은 뒤일 수 있다 — 쓰기는 이미 커밋됐을 수 있다.
                    failure = ApiError(NO_RESPONSE.format(url=self.credentials.url), status=None)
                else:
                    if response.is_success:
                        return response
                    failure = _error_for(response)
                    if response.status_code != 503:
                        raise failure
                    retry_after = retry_after_seconds(response)
                if not retryable:
                    raise failure
                backoff = random.random() * min(
                    BACKOFF_CAP_SECONDS, BACKOFF_START_SECONDS * 2**attempt
                )
                delay = max(retry_after, backoff)
                if waited + delay > BACKOFF_BUDGET_SECONDS:
                    raise failure
                if not notified:
                    notified = True
                    print(RETRYING, file=sys.stderr)
                self._sleep(delay)
                waited += delay
                attempt += 1
