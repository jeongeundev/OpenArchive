"""사용자 CLI 명령 — REST에 본인 API 토큰으로만 붙는다 (#189, ADR-057).

운영자 CLI(`cli.py`의 `--dsn`·`--user`)와 달리 DB에 붙지 않고 DSN·`.env`를 읽지 않는다.
규칙(열람 범위·쓰기 권한)은 서버가 판정한다 — 여기서는 요청하고 결과를 출력한다.
세션 전용 동작(열람 범위 변경·공유·토큰 발급·관리)은 두지 않는다 (ADR-057 결정 3).
"""

from __future__ import annotations

from datetime import datetime

from openarchive.client import (
    LOGIN_AGAIN,
    ApiClient,
    ApiError,
    Credentials,
    NotLoggedIn,
    save_credentials,
)

NEED_LOGIN = (
    "로그인이 필요합니다. openarchive login --url <서버> --token <API 토큰>으로 먼저 로그인하세요."
)


def _local_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")


def run_login(*, url: str, token: str) -> int:
    url = url.rstrip("/")
    if not url.startswith(("http://", "https://")):
        print("--url은 http:// 또는 https://로 시작해야 합니다.")
        return 2
    credentials = Credentials(url=url, token=token)
    try:
        me = ApiClient(credentials).request("GET", "/api/auth/me").json()
    except ApiError as error:
        # 401·403(공유 토큰은 사람 계정이 아니다)은 같은 문구 — 저장하지 않는다.
        print(LOGIN_AGAIN if error.status in (401, 403) else error.message)
        return 1
    if not me.get("authenticated") or not me.get("username"):
        print(LOGIN_AGAIN)
        return 1
    save_credentials(credentials)
    print(f"{me['username']}(으)로 로그인했습니다.")
    return 0


def run_whoami() -> int:
    try:
        api = ApiClient.from_saved()
        me = api.request("GET", "/api/auth/me").json()
    except NotLoggedIn:
        print(NEED_LOGIN)
        return 1
    except ApiError as error:
        print(LOGIN_AGAIN if error.status in (401, 403) else error.message)
        return 1
    if not me.get("authenticated"):
        print(LOGIN_AGAIN)
        return 1
    expires_at = me.get("expires_at")
    print(f"사용자: {me['username']}")
    print(f"토큰 범위: {me.get('scope') or '-'}")
    print(f"만료일: {_local_time(expires_at) if expires_at else '만료 없음'}")
    print(f"서버: {api.credentials.url}")
    return 0
