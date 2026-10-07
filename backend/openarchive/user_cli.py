"""사용자 CLI 명령 — REST에 본인 API 토큰으로만 붙는다 (#189, ADR-057).

운영자 CLI(`cli.py`의 `--dsn`·`--user`)와 달리 DB에 붙지 않고 DSN·`.env`를 읽지 않는다.
규칙(열람 범위·쓰기 권한)은 서버가 판정한다 — 여기서는 요청하고 결과를 출력한다.
세션 전용 동작(열람 범위 변경·공유·토큰 발급·관리)은 두지 않는다 (ADR-057 결정 3).
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from uuid import UUID

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


# ── 문서 읽기: doc list · doc show · doc download ───────────────────────────

NOT_FOUND = "문서를 찾을 수 없습니다."
PAGE_SIZE = 100  # 서버 목록 limit 상한

# 웹 DocumentStatusBadge와 같은 라벨 (D7)
EXTRACTION_LABELS = {"pending": "텍스트 인식 중", "failed": "텍스트 인식 실패"}
EMBEDDING_LABELS = {
    "pending": "대기 중",
    "processing": "처리 중…",
    "ready": "완료",
    "error": "실패",
}


def _document_path(document_id: str) -> str | None:
    """UUID가 아니면 None — 서버에 보내지 않고 같은 '찾을 수 없음'으로 끝낸다."""
    try:
        return f"/api/documents/{UUID(document_id)}"
    except ValueError:
        return None


def _status_label(item: dict) -> str:
    extraction = item.get("extraction_status")
    if extraction in EXTRACTION_LABELS:
        return EXTRACTION_LABELS[extraction]
    embedding = item.get("embedding_status")
    return EMBEDDING_LABELS.get(embedding, embedding or "-")


def _run(action: Callable[[ApiClient], int]) -> int:
    """공통 오류 처리 — 로그인 없음·서버 오류를 문구로 바꾸고 종료 코드를 정한다."""
    try:
        return action(ApiClient.from_saved())
    except NotLoggedIn:
        print(NEED_LOGIN)
        return 1
    except ApiError as error:
        print(error.message)
        return 1


def run_doc_list() -> int:
    def action(api: ApiClient) -> int:
        items: list[dict] = []
        while True:
            page = api.request(
                "GET",
                "/api/documents",
                params={"sort": "updated", "limit": PAGE_SIZE, "offset": len(items)},
            ).json()
            items += page
            if len(page) < PAGE_SIZE:
                break
        if not items:
            print("문서가 없습니다.")
            return 0
        rows = [
            (item["id"], item["title"], item["content_type"], f"v{item['version']}", _status_label(item))
            for item in items
        ]
        header = ("ID", "제목", "유형", "버전", "처리 상태")
        title_width = min(40, max(len(header[1]), *(len(row[1]) for row in rows)))
        for row in (header, *rows):
            print(
                f"{row[0]:<36}  {row[1]:<{title_width}}  {row[2]:<5}  {row[3]:<5}  {row[4]}"
            )
        return 0

    return _run(action)


def run_doc_show(*, document_id: str, version: int | None) -> int:
    path = _document_path(document_id)
    if path is None:
        print(NOT_FOUND)
        return 1

    def action(api: ApiClient) -> int:
        target = path if version is None else f"{path}/versions/{version}"
        content = api.request("GET", target).json()["content"]
        sys.stdout.write(content if content.endswith("\n") else content + "\n")
        return 0

    return _run(action)


def _filename_from(response) -> str | None:
    disposition = response.headers.get("Content-Disposition", "")
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", disposition)
    if encoded:
        return unquote(encoded.group(1).strip())
    plain = re.search(r'filename="([^"]*)"', disposition)
    return plain.group(1) if plain else None


def run_doc_download(*, document_id: str, output: Path | None) -> int:
    path = _document_path(document_id)
    if path is None:
        print(NOT_FOUND)
        return 1

    def action(api: ApiClient) -> int:
        response = api.request("GET", f"{path}/file")
        target = output
        if target is None:
            # 서버가 준 이름의 경로 성분은 버린다 — 현재 디렉터리 밖에 쓰지 않는다.
            name = Path((_filename_from(response) or "").replace("\\", "/")).name
            if name in ("", ".", ".."):
                name = str(UUID(document_id))
            target = Path(name)
        data = response.content
        try:
            with target.open("xb") as file:
                file.write(data)
        except FileExistsError:
            print(f"이미 있는 파일입니다: {target}")
            return 1
        digest = hashlib.sha256(data).hexdigest()
        print(f"{target}에 저장했습니다 ({len(data)} 바이트, sha256 {digest[:12]}…)")
        return 0

    return _run(action)
