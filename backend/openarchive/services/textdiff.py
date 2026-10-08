"""텍스트 버전 비교 — Myers 줄 비교에 작업량 예산을 둔다 (#190).

표준 `difflib`(Ratcliff/Obershelp)은 반복 줄이 많은 텍스트에서 바뀐 대목마다 후보를 다시 훑어
같은 줄의 짝 수 × 바뀐 대목 수에 비례해 느려졌다(빈 줄로 나눈 마크다운 2,000문단을 10문단마다
고치면 23.6초). git의 기본 알고리즘인 Myers O((N+M)D)는 바뀐 줄 수(D)가 작으면 문서가 길어도
빠르고, 바뀐 줄 수가 최소인 비교를 낸다. 작업량을 세다가 예산을 넘으면 계산을 멈춘다 — jsdiff의
`maxEditLength`와 같은 방식이며, 이것만이 최악 시간을 보장한다(예측식으로 미리 거르는 방식은
입력의 다른 축에서 깨졌다).
"""

from array import array

# 작업 단위: 대각선 하나 + 직진한 줄 하나 + 역추적 기록 한 칸. 실측(2026-10-08, 크기·반복도·
# 고친 곳 수·분포·전체 교체를 모두 흔듦)에서 작업 100만당 0.12~0.15초·기록 3.8MB로 일정했다 —
# 500만이면 최악 약 0.75초·19MB다. 작업량은 바뀐 줄 수(D)에 따라 대략 D²로 늘어 고친 줄이
# 약 1,100줄을 넘으면 예산 초과다. 실제 규정 개정 쌍 21개는 최대 6만 작업이었다.
MAX_DIFF_WORK = 5_000_000

Opcode = tuple[str, int, int, int, int]


def diff_hunks(old: str, new: str, context: int = 3) -> list[dict] | None:
    """줄 단위 비교 결과를 바뀐 곳 앞뒤 `context`줄씩 덩어리로 묶는다. 예산을 넘으면 None."""
    a, b = old.splitlines(), new.splitlines()
    codes, _ = _myers(a, b, MAX_DIFF_WORK)
    if codes is None:
        return None
    hunks = []
    for group in group_opcodes(codes, context):
        lines = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines.extend({"op": "equal", "text": text} for text in a[i1:i2])
            if tag in ("delete", "replace"):
                lines.extend({"op": "removed", "text": text} for text in a[i1:i2])
            if tag in ("insert", "replace"):
                lines.extend({"op": "added", "text": text} for text in b[j1:j2])
        hunks.append({"lines": lines})
    return hunks


def opcodes(a: list[str], b: list[str], *, budget: int) -> list[Opcode] | None:
    """`difflib.SequenceMatcher.get_opcodes()`와 같은 형식의 편집 구간. 예산을 넘으면 None."""
    return _myers(a, b, budget)[0]


def _myers(a: list[str], b: list[str], budget: int) -> tuple[list[Opcode] | None, int]:
    """(편집 구간 또는 None, 실제로 쓴 작업량).

    예산은 대각선마다 한 곳에서 확인한다. 확인 직전 작업량은 예산 이하이고 대각선 하나는
    2 + 직진 줄 수(≤ min(N, M))만 더하므로, 쓴 작업량은 예산 + 2 + min(N, M)을 넘지 않는다.
    """
    prefix = 0
    while prefix < len(a) and prefix < len(b) and a[prefix] == b[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < len(a) - prefix
        and suffix < len(b) - prefix
        and a[-1 - suffix] == b[-1 - suffix]
    ):
        suffix += 1
    # 줄 비교를 정수 비교로 바꾼다 — 긴 줄을 직진마다 다시 비교하지 않는다.
    ids: dict[str, int] = {}
    left = [ids.setdefault(line, len(ids)) for line in a[prefix : len(a) - suffix]]
    right = [ids.setdefault(line, len(ids)) for line in b[prefix : len(b) - suffix]]
    n, m = len(left), len(right)

    blocks = [(0, 0, prefix)]
    work = 0
    if n and m:
        offset = n + m + 1
        v = array("i", [0]) * (2 * offset + 1)
        trace: list[array] = []
        end = None
        for d in range(n + m + 1):
            # d번째 걸음 직전의 상태. 역추적은 대각선 k ∈ [-d, d]를 k + d 자리로 읽는다.
            # 이 기록(2d + 1칸)은 아래에서 대각선마다 2씩 센다.
            trace.append(v[offset - d : offset + d + 1])
            for k in range(-d, d + 1, 2):
                if k == -d or (k != d and v[offset + k - 1] < v[offset + k + 1]):
                    x = v[offset + k + 1]
                else:
                    x = v[offset + k - 1] + 1
                y = x - k
                start = x
                while x < n and y < m and left[x] == right[y]:
                    x += 1
                    y += 1
                work += 2 + x - start
                v[offset + k] = x
                if x >= n and y >= m:
                    end = d
                    break
                if work > budget:
                    return None, work
            if end is not None:
                break
        blocks += [(prefix + i, prefix + j, size) for i, j, size in _backtrack(trace, end, n, m)]
    blocks.append((len(a) - suffix, len(b) - suffix, suffix))
    return _blocks_to_opcodes(blocks, len(a), len(b)), work


def _backtrack(trace: list[array], end: int, n: int, m: int) -> list[tuple[int, int, int]]:
    """끝점에서 거꾸로 걸어 같은 줄 구간(i, j, 길이)을 앞에서부터 돌려준다."""
    x, y = n, m
    snakes = []
    for d in range(end, 0, -1):
        previous = trace[d]
        k = x - y
        if k == -d or (k != d and previous[k - 1 + d] < previous[k + 1 + d]):
            prev_k = k + 1  # 오른쪽 줄 하나를 넣었다 — x는 그대로다
            start_x = previous[prev_k + d]
        else:
            prev_k = k - 1  # 왼쪽 줄 하나를 지웠다 — x가 하나 늘었다
            start_x = previous[prev_k + d] + 1
        if x > start_x:
            snakes.append((start_x, start_x - k, x - start_x))
        x = previous[prev_k + d]
        y = x - prev_k
    if x > 0:
        snakes.append((0, 0, x))
    return snakes[::-1]


def _blocks_to_opcodes(blocks: list[tuple[int, int, int]], len_a: int, len_b: int) -> list[Opcode]:
    """같은 줄 구간을 difflib `get_opcodes`와 같은 규칙으로 편집 구간으로 바꾼다."""
    merged: list[list[int]] = []
    for i, j, size in blocks:
        if not size:
            continue
        if merged and merged[-1][0] + merged[-1][2] == i and merged[-1][1] + merged[-1][2] == j:
            merged[-1][2] += size
        else:
            merged.append([i, j, size])
    codes: list[Opcode] = []
    i = j = 0
    for ai, bj, size in [*merged, [len_a, len_b, 0]]:
        if i < ai and j < bj:
            codes.append(("replace", i, ai, j, bj))
        elif i < ai:
            codes.append(("delete", i, ai, j, bj))
        elif j < bj:
            codes.append(("insert", i, ai, j, bj))
        if size:
            codes.append(("equal", ai, ai + size, bj, bj + size))
        i, j = ai + size, bj + size
    return codes


def group_opcodes(codes: list[Opcode], n: int = 3) -> list[list[Opcode]]:
    """`difflib.SequenceMatcher.get_grouped_opcodes`와 같은 묶음 — 바뀐 곳 앞뒤 n줄만 남긴다."""
    codes = list(codes) or [("equal", 0, 1, 0, 1)]
    if codes[0][0] == "equal":
        tag, i1, i2, j1, j2 = codes[0]
        codes[0] = tag, max(i1, i2 - n), i2, max(j1, j2 - n), j2
    if codes[-1][0] == "equal":
        tag, i1, i2, j1, j2 = codes[-1]
        codes[-1] = tag, i1, min(i2, i1 + n), j1, min(j2, j1 + n)
    groups = []
    group: list[Opcode] = []
    for tag, i1, i2, j1, j2 in codes:
        if tag == "equal" and i2 - i1 > 2 * n:
            group.append((tag, i1, min(i2, i1 + n), j1, min(j2, j1 + n)))
            groups.append(group)
            group = []
            i1, j1 = max(i1, i2 - n), max(j1, j2 - n)
        group.append((tag, i1, i2, j1, j2))
    if group and not (len(group) == 1 and group[0][0] == "equal"):
        groups.append(group)
    return groups
