"""텍스트 버전 비교의 순수 계산부 — Myers 줄 비교와 작업량 예산 (#190)."""

import difflib
import random
import time

import pytest

from openarchive.services import textdiff

LINES = ["", "가", "나", "- 항목"]


def random_pair(rnd):
    prefix = [f"앞 {i}" for i in range(rnd.randint(0, 3))]
    suffix = [f"뒤 {i}" for i in range(rnd.randint(0, 3))]
    a = prefix + [rnd.choice(LINES) for _ in range(rnd.randint(0, 15))] + suffix
    b = prefix + [rnd.choice(LINES) for _ in range(rnd.randint(0, 15))] + suffix
    return a, b


def apply(opcodes, a, b):
    """opcodes를 a에 적용해 만든 줄 목록. equal 구간이 실제로 같은지도 확인한다."""
    out = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            assert a[i1:i2] == b[j1:j2]
            out += a[i1:i2]
        else:
            out += b[j1:j2]
    return out


def edits(opcodes):
    return sum((i2 - i1) + (j2 - j1) for tag, i1, i2, j1, j2 in opcodes if tag != "equal")


def test_opcodes_rebuild_the_new_version_with_minimal_edits():
    rnd = random.Random(190)
    for _ in range(500):
        a, b = random_pair(rnd)
        codes = textdiff.opcodes(a, b, budget=10**9)
        assert apply(codes, a, b) == b
        # 연속 구간이 두 목록을 빈틈없이 덮는다 — difflib get_opcodes와 같은 형식이다.
        assert [c[1] for c in codes[1:]] == [c[2] for c in codes[:-1]]
        assert [c[3] for c in codes[1:]] == [c[4] for c in codes[:-1]]
        if codes:
            assert (codes[0][1], codes[0][3]) == (0, 0)
            assert (codes[-1][2], codes[-1][4]) == (len(a), len(b))
        else:
            assert a == b == []
        # Myers는 바뀐 줄 수가 최소인 비교를 낸다 — difflib보다 많을 수 없다.
        matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
        assert edits(codes) <= edits(matcher.get_opcodes())


def test_adjacent_delete_and_insert_become_replace():
    assert textdiff.opcodes(["가", "나", "다"], ["가", "라", "다"], budget=10**9) == [
        ("equal", 0, 1, 0, 1), ("replace", 1, 2, 1, 2), ("equal", 2, 3, 2, 3),
    ]


@pytest.mark.parametrize("gap", [5, 6, 7, 8])
def test_grouping_boundary_matches_difflib(gap):
    """바뀐 곳 사이 같은 줄이 2 × 3줄 안팎일 때 — 하나로 묶을지 나눌지의 경계다."""
    a = ["처음"] + [f"같음 {i}" for i in range(gap)] + ["끝"]
    b = ["처음 바뀜"] + [f"같음 {i}" for i in range(gap)] + ["끝 바뀜"]
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    assert textdiff.group_opcodes(matcher.get_opcodes(), 3) == list(matcher.get_grouped_opcodes(3))
    assert len(textdiff.group_opcodes(matcher.get_opcodes(), 3)) == (1 if gap <= 6 else 2)


def test_grouping_matches_difflib():
    rnd = random.Random(7)
    for _ in range(300):
        a = [rnd.choice(LINES + [f"고유 {i}" for i in range(5)]) for _ in range(rnd.randint(0, 40))]
        b = [rnd.choice(LINES + [f"고유 {i}" for i in range(5)]) for _ in range(rnd.randint(0, 40))]
        matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
        assert textdiff.group_opcodes(matcher.get_opcodes(), 3) == list(matcher.get_grouped_opcodes(3))


def test_diff_hunks_lines():
    assert textdiff.diff_hunks("가\n나\n다", "가\n나2\n다\n라") == [{"lines": [
        {"op": "equal", "text": "가"}, {"op": "removed", "text": "나"},
        {"op": "added", "text": "나2"}, {"op": "equal", "text": "다"},
        {"op": "added", "text": "라"},
    ]}]


def test_only_terminal_newline_differs():
    assert textdiff.diff_hunks("가\n", "가") == []


def test_budget_exceeded_returns_none(monkeypatch):
    old = "\n".join(f"줄 {i}" for i in range(30))
    new = old.replace("줄 15", "바뀜")
    assert textdiff.diff_hunks(old, new) is not None
    monkeypatch.setattr(textdiff, "MAX_DIFF_WORK", 1)
    assert textdiff.diff_hunks(old, new) is None


def test_whole_rewrite_is_too_large():
    """문서 대부분이 바뀌면 편집량이 예산을 넘는다 — 계산하지 않고 None이다."""
    old = "\n".join(f"이전 {i}" for i in range(3000))
    new = "\n".join(f"새 {i}" for i in range(3000))
    assert textdiff.diff_hunks(old, new) is None


@pytest.mark.parametrize("step", [3, 10])
def test_frequent_edits_on_repeated_blank_lines_stay_fast(step):
    """difflib(autojunk=False)으로는 9.8초·23.6초 걸리던 입력이다 (#190 재리뷰 재현).

    빈 줄로 문단을 나눈 마크다운에서 step 문단마다 고친다. 시간 상한은 예산이 보장하고,
    여기서는 이 입력이 예산 안에서 실제 비교 결과를 낸다는 것을 확인한다.
    """
    paragraphs = 2000
    old_lines = [f"문단 {i} 내용입니다." if i % 2 == 0 else "" for i in range(2 * paragraphs)]
    new_lines = [
        line + " (수정)" if i % (2 * step) == 0 else line for i, line in enumerate(old_lines)
    ]
    started = time.perf_counter()
    hunks = textdiff.diff_hunks("\n".join(old_lines), "\n".join(new_lines))
    assert time.perf_counter() - started < 2
    assert hunks is not None
    removed = [line for hunk in hunks for line in hunk["lines"] if line["op"] == "removed"]
    assert len(removed) == len(range(0, 2 * paragraphs, 2 * step))


def test_work_never_runs_far_past_the_budget():
    """예산을 넘는 순간 멈춘다 — 쓴 작업량은 예산 + 2 + min(N, M)을 넘지 않는다.

    두 종류 줄만으로 된 입력은 여러 대각선에 직진이 동시에 생긴다 — 걸음(d) 단위로만 확인하면
    한 걸음 안의 직진이 쌓여 이 한계를 넘는다(그 구현에서 3,000건 중 실제로 넘는 입력이 나온다).
    """
    rnd = random.Random(2026)
    for _ in range(3000):
        a = [rnd.choice(["", "가"]) for _ in range(rnd.randint(1, 60))]
        b = [rnd.choice(["", "가"]) for _ in range(rnd.randint(1, 60))]
        full = textdiff._myers(a, b, budget=10**9)[1]
        codes, work = textdiff._myers(a, b, budget=full)
        assert codes is not None and work == full
        if full == 0:
            continue
        budget = rnd.randrange(full)
        codes, work = textdiff._myers(a, b, budget=budget)
        # 마지막 대각선에서 끝에 닿으면 그 걸음은 마치고 결과를 낸다 — 그때 작업량은 전체와 같다.
        assert codes is None and work > budget or codes is not None and work == full
        assert work <= budget + 2 + min(len(a), len(b))
