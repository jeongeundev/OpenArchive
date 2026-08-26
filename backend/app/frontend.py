"""빌드된 프론트엔드를 API와 같은 오리진에서 서빙한다 (ADR-041).

Node 런타임을 사용자 쪽에서 없애기 위한 것이다. `STATIC_EXPORT=1 npm run build`가
내놓은 산출물을 패키지에 동봉하고, 여기서 그대로 내려준다. 같은 오리진이므로 개발
서버가 쓰던 `/api/*` 프록시(next.config의 rewrites)도 필요 없어진다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response

# 정적 export가 문서 상세 자리에 뽑아 두는 껍데기 세그먼트. 프론트의
# `generateStaticParams`가 내놓는 값과 같아야 한다 (`documents/[id]/page.tsx`).
FALLBACK_SEGMENT = "__id__"

DEFAULT_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _resolve(static_dir: Path, relative: str) -> Path | None:
    """산출물 안의 파일만 돌려준다. 바깥을 가리키면 None.

    `..`가 섞인 경로는 정규화 뒤에 판정해야 한다 — 문자열만 검사하면 인코딩된
    형태를 놓친다.
    """
    candidate = (static_dir / relative).resolve()
    if not candidate.is_relative_to(static_dir):
        return None
    return candidate if candidate.is_file() else None


def _fallback_path(path: str) -> str | None:
    """두 번째 세그먼트를 껍데기로 바꾼 경로. 동적 라우트의 실제 ID를 받아내는 자리다.

    확장자는 남긴다. `<Link>` 이동은 HTML이 아니라 RSC 페이로드(.txt)를 받아 가므로,
    확장자를 잃으면 라우터가 페이로드 대신 HTML을 읽어 클라이언트 이동이 깨진다.

    바꿔치기한 경로가 실제로 존재할 때만 쓰이므로, 어느 라우트가 동적인지를 여기에
    적어 둘 필요가 없다 — 목록을 손으로 적으면 라우트가 늘 때 조용히 낡는다.
    """
    parts = path.split("/")
    if len(parts) < 2:
        return None
    name, dot, suffix = parts[1].partition(".")
    if not name:
        return None
    parts[1] = FALLBACK_SEGMENT + dot + suffix
    return "/".join(parts)


def _candidates(path: str) -> list[str]:
    """찾아볼 순서.

    정적 export는 `/search`를 `search.html`로 내놓으므로 확장자 없는 요청에 `.html`을
    붙여 한 번 더 본다. 껍데기 치환에도 같은 보정이 필요하다 — `documents/<id>`는
    `documents/__id__.html`이 받아야 하고, 확장자가 이미 붙은 `documents/<id>.txt`는
    `documents/__id__.txt`가 그대로 받는다.
    """
    direct = path or "index.html"
    found = [direct, f"{direct}.html"]
    fallback = _fallback_path(path)
    if fallback is not None:
        found += [fallback, f"{fallback}.html"]
    return found


def mount_frontend(app: FastAPI, static_dir: Path = DEFAULT_STATIC_DIR) -> bool:
    """빌드 산출물이 있으면 서빙 라우트를 더한다. 없으면 아무것도 하지 않는다.

    개발 중에는 `npm run dev`가 3000번에서 도는 것이 정상이므로, 산출물이 없다고
    해서 실패로 다루지 않는다. 돌려주는 값은 "붙였는가"이다.
    """
    if not static_dir.is_dir():
        return False
    root = static_dir.resolve()
    not_found = root / "404.html"

    # 라우터의 마지막에 등록되므로 앞서 선언된 API 라우트가 항상 먼저 잡힌다.
    # HEAD를 함께 받는 이유는 Next의 `<Link>` 프리페치가 HEAD로 오기 때문이다 —
    # GET만 받으면 화면은 멀쩡한데 콘솔이 405로 뒤덮인다 (실측).
    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def serve(path: str) -> Response:
        for candidate in _candidates(path):
            resolved = _resolve(root, candidate)
            if resolved is not None:
                return FileResponse(resolved)
        if not_found.is_file():
            return FileResponse(not_found, status_code=404)
        return Response(status_code=404)

    return True
