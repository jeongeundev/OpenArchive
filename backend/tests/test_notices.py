"""한컴 HWP 형식 공개 조건의 고지 문구 (ADR-059 결정 1).

HWP 파서는 한컴이 공개한 「한글 문서 파일 형식 5.0」을 기준으로 만들었다. 공개 조건은 개발
결과물의 UI·매뉴얼·도움말·소스에 아래 문장을 모두 적으라고 한다
(https://store.hancom.com/etc/hwpDownload.do). UI(웹 하단)는 `SiteFooter.test.tsx`가 본다.
"""

from pathlib import Path

import pytest

from openarchive.cli import main

HANCOM_NOTICE = "본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다."
ROOT = Path(__file__).resolve().parents[2]


def test_cli_help_carries_the_hancom_notice(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert HANCOM_NOTICE in capsys.readouterr().out


@pytest.mark.parametrize(
    "path",
    ["README.md", "backend/README.md", "backend/openarchive/services/parsing.py"],
)
def test_manual_and_source_carry_the_hancom_notice(path):
    assert HANCOM_NOTICE in (ROOT / path).read_text(encoding="utf-8")
