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
from uuid import UUID, uuid4

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


# ── 문서 쓰기: upload · edit · restore · tag · delete · trash ───────────────
# 쓰기 권한·소유자·열람 판정은 서버가 한다 — 여기서 미리 막지 않는다 (ADR-057 결정 1).


def _conflict_message(error: ApiError) -> str:
    """버전 충돌 409(`current_version` 있음)만 CLI 문구로 바꾼다 (D6)."""
    current = (error.body or {}).get("current_version")
    if error.status == 409 and current is not None:
        return (
            f"다른 곳에서 먼저 수정되었습니다. 현재 버전은 v{current}입니다 — "
            "openarchive doc show로 다시 받아 고친 뒤 실행하세요."
        )
    return error.message


def run_doc_upload(*, path: Path, title: str | None, tags: list[str]) -> int:
    if not path.is_file():
        print(f"파일이 없습니다: {path}")
        return 1

    def action(api: ApiClient) -> int:
        data: dict[str, str | list[str]] = {}
        if title is not None:
            data["title"] = title
        if tags:
            data["tags"] = tags
        # 명령 실행마다 키 하나 — 재시도에는 같은 키가 실린다 (ADR-047).
        document = api.request(
            "POST",
            "/api/documents",
            files={"file": (path.name, path.read_bytes())},
            data=data,
            headers={"Idempotency-Key": str(uuid4())},
        ).json()
        print("문서를 올렸습니다 — 처리가 끝나면 검색됩니다.")
        print(document["id"])
        return 0

    return _run(action)


def _write_with_version(action: Callable[[ApiClient], int]) -> int:
    try:
        return action(ApiClient.from_saved())
    except NotLoggedIn:
        print(NEED_LOGIN)
        return 1
    except ApiError as error:
        print(_conflict_message(error))
        return 1


def run_doc_edit(*, document_id: str, file: Path, base_version: int | None) -> int:
    path = _document_path(document_id)
    if path is None:
        print(NOT_FOUND)
        return 1
    try:
        content = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"파일이 없습니다: {file}")
        return 1
    except (UnicodeDecodeError, IsADirectoryError):
        print("UTF-8 텍스트 파일만 쓸 수 있습니다.")
        return 1

    def action(api: ApiClient) -> int:
        version = base_version
        if version is None:
            version = api.request("GET", path).json()["version"]
        document = api.request(
            "PUT", path, json={"content": content, "version": version}
        ).json()
        print(f"저장했습니다 — 새 버전 v{document['version']}")
        return 0

    return _write_with_version(action)


def run_doc_restore(*, document_id: str, version: int) -> int:
    path = _document_path(document_id)
    if path is None:
        print(NOT_FOUND)
        return 1

    def action(api: ApiClient) -> int:
        current = api.request("GET", path).json()["version"]
        document = api.request(
            "POST", f"{path}/versions/{version}/restore", json={"current_version": current}
        ).json()
        print(f"되돌렸습니다 — v{version} 내용으로 새 버전 v{document['version']}")
        return 0

    return _write_with_version(action)


def run_doc_tag(*, document_id: str, tags_csv: str) -> int:
    path = _document_path(document_id)
    if path is None:
        print(NOT_FOUND)
        return 1
    tags = [tag.strip() for tag in tags_csv.split(",") if tag.strip()]

    def action(api: ApiClient) -> int:
        saved = api.request("PUT", f"{path}/tags", json={"tags": tags}).json()["tags"]
        print(f"태그: {', '.join(saved)}" if saved else "태그를 모두 지웠습니다.")
        return 0

    return _run(action)


PERMANENT_PROMPT = "삭제하면 되돌릴 수 없습니다. 계속할까요? [y/N] "


def run_doc_delete(*, document_id: str, permanent: bool) -> int:
    path = _document_path(document_id)
    if path is None:
        print(NOT_FOUND)
        return 1
    if permanent:
        try:
            answer = input(PERMANENT_PROMPT)
        except EOFError:
            answer = ""
        if answer.strip().lower() != "y":
            print("취소했습니다.")
            return 1

    def action(api: ApiClient) -> int:
        if permanent:
            api.request("DELETE", path, params={"permanent": "true"})
            print("영구 삭제했습니다.")
        else:
            # 되돌릴 수 있는 동작이라 묻지 않는다 (ADR-060 결정 8).
            api.request("DELETE", path)
            print(
                "휴지통으로 옮겼습니다. "
                f"openarchive doc trash restore {UUID(document_id)}로 되돌릴 수 있습니다."
            )
        return 0

    return _run(action)


def run_trash_list() -> int:
    def action(api: ApiClient) -> int:
        items = api.request("GET", "/api/documents/trash").json()
        if not items:
            print("휴지통이 비어 있습니다.")
            return 0
        header = ("ID", "제목", "삭제한 때", "영구 삭제 예정")
        rows = [
            (item["id"], item["title"], _local_time(item["deleted_at"]), _local_time(item["purge_at"]))
            for item in items
        ]
        title_width = min(40, max(len(header[1]), *(len(row[1]) for row in rows)))
        for row in (header, *rows):
            print(f"{row[0]:<36}  {row[1]:<{title_width}}  {row[2]:<16}  {row[3]}")
        return 0

    return _run(action)


def run_trash_restore(*, document_id: str) -> int:
    path = _document_path(document_id)
    if path is None:
        print(NOT_FOUND)
        return 1

    def action(api: ApiClient) -> int:
        document = api.request("POST", f"{path}/restore").json()
        print(f"복원했습니다: {document['title']}")
        return 0

    return _run(action)


# ── 검색·답변: search · ask (--user 없이) ──────────────────────────────────

ASK_TIMEOUT_SECONDS = 300.0  # 생성은 수십 초 걸린다 — 서버 ANSWER_TIMEOUT_SECONDS(120) + 여유


def _snippet(text: str, width: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _query_body(query: str, tags: list[str], content_type: str | None, k: int) -> dict:
    # k 범위는 서버가 판정한다(422) — 결과도 서버의 단일 SQL 그대로 출력한다.
    return {"query": query, "tags": tags or None, "content_type": content_type, "k": k}


# 웹 `frontend/src/lib/relations.ts`와 같은 어휘.
RELATION_LABELS = {
    "overlaps": "여러 대목에서 만난다",
    "points_to": "이 대목에서 만난다",
    "refers": "본문에서 가리킨다",
    "related": "관련 있음",
    "revision": "이전 텍스트 버전",
    "broader": "더 자세한 문서",
}


def relation_label(kind: str) -> str:
    return RELATION_LABELS.get(kind, "관련 있음")


def run_search(*, query: str, tags: list[str], content_type: str | None, k: int) -> int:
    def action(api: ApiClient) -> int:
        body = _query_body(query, tags, content_type, k)
        hits = api.request("POST", "/api/search", json=body).json()["items"]
        if not hits:
            print("결과가 없습니다.")
            return 0
        for rank, hit in enumerate(hits, start=1):
            tag_text = f"  [{', '.join(hit['tags'])}]" if hit["tags"] else ""
            via = hit.get("via")
            if via is not None:
                print(f"{rank}. {hit['title']}{tag_text}")
            else:
                print(f"{rank}. {hit['title']}  {hit['score']:.3f}{tag_text}")
            print(f"   {_snippet(hit['content'])}")
            if via is not None:
                print(f"   관계로 찾음: {relation_label(via['kind'])} · {via['depth']}단계")
            print(f"   {hit['document_id']}")
        return 0

    return _run(action)


def run_ask(*, query: str, tags: list[str], content_type: str | None, k: int) -> int:
    def action(api: ApiClient) -> int:
        body = _query_body(query, tags, content_type, k)
        result = api.request("POST", "/api/ask", json=body, timeout=ASK_TIMEOUT_SECONDS).json()
        status = result["status"]
        if status == "disabled":
            print(
                "서버에서 답변 생성이 꺼져 있습니다. "
                "검색은 openarchive search로 그대로 쓸 수 있습니다."
            )
            return 1
        if status == "no_evidence":
            print("근거로 쓸 문서를 찾지 못했습니다.")
            return 0
        if status == "failed":
            print(f"{result.get('detail') or ''} 검색은 openarchive search로 그대로 쓸 수 있습니다.")
            return 1
        print("근거 기반 답변 — 근거 문서만 쓰도록 지시했지만 보장은 아닙니다.")
        print()
        print(result["answer"])
        print()
        print("근거")
        sources = result["sources"]
        for source in sources:
            if not source["cited"]:
                continue
            version = f"v{source['based_on_version']} 기준"
            if source["revised"]:
                version += f" · 현재 v{source['current_version']}"
            print(f"[{source['label']}] {source['title']} · {version}")
            print(f"    {_snippet(source['content'])}")
            print(f"    {source['document_id']} · 대목 {source['chunk_index']}")
        uncited = sum(not source["cited"] for source in sources)
        if uncited:
            print(f"인용하지 않은 근거 {uncited}건")
        return 0

    return _run(action)
