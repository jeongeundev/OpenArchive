# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""정적 번들에 든 서드파티 코드가 NOTICE.md에 고지되어 있다.

`backend/openarchive/static/`은 저장소·wheel과 함께 배포된다. 번들에 남은 식별 문자열로
구성 요소를 찾아, 그 이름과 라이선스 본문이 NOTICE.md·`backend/licenses/`에 있는지 본다.
식별 문자열은 NOTICE.md 「확인 방법」과 같은 방식으로 고른 것이다.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTICE = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
LICENSES = ROOT / "backend" / "licenses"
CHUNKS = sorted((ROOT / "backend" / "openarchive" / "static" / "_next" / "static" / "chunks").glob("*.js"))

# (번들 식별 문자열, NOTICE.md에 적힌 구성 요소 이름, 라이선스 본문 파일)
BUNDLED = [
    # SWC가 넣는 모듈 상호 운용 도우미(`_interop_require_default`)
    ("r._=function(e){return e&&e.__esModule?e:{default:e}}", "@swc/helpers", "swc-helpers-license.txt"),
    # App Router는 Next가 동봉한 React를 번들한다 — 버전은 node_modules/react가 아니라 이것이다
    ("19.3.0-canary-cbb046ab-20260731", "19.3.0-canary-cbb046ab-20260731", "react-license.txt"),
    ("createFromReadableStream", "react-server-dom-turbopack", "react-license.txt"),
    # Next.js `polyfill-nomodule` — 구형 브라우저용 폴리필 묶음
    ("zloirock", "core-js", "core-js-license.txt"),
    ("Network request failed", "whatwg-fetch", "whatwg-fetch-license.txt"),
    ("Object.assign cannot be called with null or undefined", "object-assign", "object-assign-license.txt"),
]


def _bundle() -> str:
    return "".join(p.read_text(encoding="utf-8", errors="ignore") for p in CHUNKS)


def test_static_bundle_exists():
    assert CHUNKS, "정적 번들이 없다 — npm run build:static"


@pytest.mark.parametrize(("signature", "component", "license_file"), BUNDLED)
def test_bundled_component_is_noticed(signature, component, license_file):
    if signature not in _bundle():
        pytest.fail(f"번들에 {component} 식별 문자열이 없다 — 의존성이 바뀌었으면 이 표와 NOTICE.md를 함께 고친다")
    assert component in NOTICE
    assert license_file in NOTICE
    assert (LICENSES / license_file).is_file()


def test_every_license_file_is_listed_in_notice():
    missing = [p.name for p in LICENSES.iterdir() if p.name not in NOTICE]
    assert missing == []
