#!/usr/bin/env python3
"""HTTP만으로 OpenArchive에 텍스트 문서를 공급하는 독립 클라이언트 예제.

이 파일은 ``backend``를 import하지 않으며 Python 표준 라이브러리만 사용한다.

실행 예시::

    python examples/ingest_text.py --base-url http://localhost:8000 \
      --username alice --password secret --title "API 문서" document.md
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

LOGIN_PATH = "/api/auth/login"
INGEST_PATH = "/api/documents/text"
DOCUMENT_PATH = "/api/documents/{document_id}"

# 임베딩이 아직 끝나지 않은 상태. 워커가 잡을 집으면 pending → processing으로 넘어가므로,
# pending만 기다리면 그 순간 폴링이 끝나 미완 문서를 결과로 내놓는다.
IN_PROGRESS_STATUSES = frozenset({"pending", "processing"})


def is_in_progress(document: dict[str, Any]) -> bool:
    return document["embedding_status"] in IN_PROGRESS_STATUSES


def _request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with opener.open(request) as response:
        return json.load(response)


def _read_content(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text()


def ingest(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    _request_json(
        opener,
        base_url + LOGIN_PATH,
        method="POST",
        body={"username": args.username, "password": args.password},
    )
    document = _request_json(
        opener,
        base_url + INGEST_PATH,
        method="POST",
        body={
            "title": args.title,
            "content": _read_content(args.input),
            "content_type": args.content_type,
            "tags": args.tags,
            "visibility": args.visibility,
        },
    )

    deadline = time.monotonic() + args.timeout
    while is_in_progress(document) and time.monotonic() < deadline:
        time.sleep(args.poll_interval)
        path = DOCUMENT_PATH.format(document_id=document["id"])
        document = _request_json(opener, base_url + path)
    return document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="-", help="텍스트 파일 경로 (기본: stdin)")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--content-type", choices=("txt", "md"), default="md")
    parser.add_argument("--tags", nargs="*", default=[])
    parser.add_argument("--visibility", choices=("public", "private"), default="public")
    parser.add_argument("--timeout", type=float, default=30.0, help="폴링 제한 시간(초)")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="폴링 간격(초)")
    return parser.parse_args()


def main() -> None:
    print(json.dumps(ingest(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
