"""문서 본문을 임베딩 단위로 자르는 순수 함수 (ARCHITECTURE.md "청킹").

DB도 모델도 파일시스템도 시간도 건드리지 않는다. 워커의 나머지 부분(트랜잭션·잠금·
재시도)과 분리해 두어야 청킹 규칙만 밀리초 단위로 검증할 수 있다. 청크 경계는 완결된
문단, 문장·조문 머리, 그 밖의 문단 순으로 고른다.

길이 단위는 **문자 수**다. 토크나이저로 세면 모델 의존성이 생겨 순수 함수가 아니게
되고, BGE-M3의 8192 토큰 한도에 1,000자는 충분히 여유가 있다 (ADR-003).
"""

import re

# 빈 줄 = 문단 경계. 줄 끝에 공백이 남아 있어도 경계로 본다 — 편집기나 PDF 파서를
# 거친 텍스트에서 흔하다. `[^\S\n]`은 개행을 뺀 공백이다.
_PARAGRAPH_BREAK = re.compile(r"\n[^\S\n]*\n")

# 한국어 서술 종결과 공백 앞 문장 부호를 잡는다. 숫자 바로 뒤 마침표는 목록 번호와
# 날짜(`1.`, `2021. 12. 28.`)일 수 있으므로 일반 문장 부호 후보에서 제외한다.
_SENTENCE_END = re.compile(r"(?:다|요)\.|(?<!\d)[.!?。](?=\s|$)")

# 붙어 있는 법령·규정 조문 머리는 앞 문장의 종결 부호가 누락되어도 강한 구조 경계다.
_ARTICLE_HEAD = re.compile(r"제\s?\d+\s?조(?:의\s?\d+)?\s?\(")

# 빈 줄 직전의 짧은 Markdown 구조 문단은 종결 부호가 없어도 완결된 문단으로 본다.
# 긴 PDF 한 줄의 우연한 접두사 일치를 막기 위해 호출부에서 200자 이하에만 적용한다.
# 헤딩은 넣지 않는다 — 헤딩으로 끝나는 자리는 등급을 매기기 전에 후보에서 빠진다.
_MARKDOWN_STRUCTURE_LINE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|\||>|```|---)")

# 앞 조각이 ATX 헤딩 줄로 끝나게 하는 자리는 등급과 무관하게 경계가 아니다 — 헤딩이 앞 청크
# 꼬리에 고립된다(#93 K3). 헤딩 뒤 빈 줄뿐 아니라 `### 왜 그런가?`의 `?`, 헤딩으로 끝나는
# 혼합 문단의 빈 줄도 같다. `match(pos)`로 줄 첫머리에 맞추므로 `^`를 두지 않는다.
_ATX_HEADING_LINE = re.compile(r"\s*#{1,6}\s+\S")

_PARAGRAPH_TERMINATORS = frozenset('.!?。」』)]"\'”’:')


def chunk_text(text: str, *, max_chars: int = 1000, overlap: int = 150) -> list[str]:
    """문서 본문을 임베딩 단위로 자른다. 경계 후보의 의미 등급을 우선한다.

    `max_chars` 폭의 창을 앞으로 밀며 조각을 만든다. 창 안에서 완결된 문단 경계,
    문장 끝·조문 머리, 그 밖의 문단 경계 순으로 고르고, 후보가 없으면 창 끝에서
    강제로 자른다. 같은 등급에서는 뒤쪽 후보가 이긴다. 다음 창은 직전 조각의 끝에서
    `overlap`만큼 되돌아간 자리에서 시작하므로 인접 조각이 텍스트를 공유한다.
    오버랩이 창 **안에** 들어 있으므로, 덧붙인 부분까지 합쳐 `max_chars`를 넘지 않는다.

    같은 입력에는 항상 같은 출력을 낸다. 재임베딩이 멱등하려면 이 성질이 필요하다 —
    같은 본문을 다시 처리했는데 청크 경계가 달라지면 검색 결과가 이유 없이 흔들린다.

    빈 문자열이나 공백뿐인 본문은 빈 리스트를 반환한다.

    Raises:
        ValueError: `max_chars`가 1 미만이거나, `overlap`이 음수 또는 `max_chars` 이상일 때.
            `overlap >= max_chars`면 창의 전진 폭이 0 이하가 되어 분할이 끝나지 않는다.
    """
    return [text[start:end] for start, end in chunk_spans(text, max_chars=max_chars, overlap=overlap)]


def chunk_spans(text: str, *, max_chars: int = 1000, overlap: int = 150) -> list[tuple[int, int]]:
    """`chunk_text`의 각 청크가 원문 `text`의 어디인지 `(start, end)`로 돌려준다.

    `text[start:end]`가 곧 같은 번호의 청크다. 답변 인용이 「그 버전의 그 자리」를
    가리키려면 청크 번호를 원문 위치로 되돌려야 한다 (#96 b).
    """
    if max_chars < 1:
        raise ValueError(f"max_chars는 1 이상이어야 한다: {max_chars}")
    if not 0 <= overlap < max_chars:
        raise ValueError(
            f"overlap은 0 이상 max_chars 미만이어야 한다: {overlap} (max_chars={max_chars})"
        )

    body = text.strip()
    if not body:
        return []
    offset = len(text) - len(text.lstrip())

    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        limit = start + max_chars
        if limit >= len(body):
            end = len(body)
        else:
            cut = _boundary_cut(body, start, limit, floor=start + overlap)
            end = cut if cut is not None else limit

        segment = body[start:end]
        piece = segment.strip()
        if piece:
            piece_start = offset + start + len(segment) - len(segment.lstrip())
            spans.append((piece_start, piece_start + len(piece)))

        if end >= len(body):
            return spans
        start = end - overlap


def _boundary_cut(body: str, start: int, limit: int, floor: int) -> int | None:
    """`[start, limit)` 안에서 가장 높은 등급의 마지막 경계. 없으면 None.

    후보 등급은 완결된 문단, 문장 끝·조문 머리, 문장 중간 문단 순이다. 같은 등급의
    마지막 경계를 골라 창을 최대한 채운다. 앞 조각을 헤딩 줄로 끝내는 자리는 등급을
    매기기 전에 뺀다.

    `floor`(= start + overlap) 이하의 경계를 버리는 이유는 전진 보장이다. 다음 창은
    `cut - overlap`에서 시작하므로, 경계가 그보다 앞이면 창이 제자리이거나 뒤로
    물러난다. 버려진 경계는 다음 조각 안쪽에 그대로 남으므로 내용은 잃지 않는다.
    """
    completed_paragraphs: list[int] = []
    incomplete_paragraphs: list[int] = []
    paragraph_start = start
    for match in _PARAGRAPH_BREAK.finditer(body, start, limit):
        paragraph = body[paragraph_start : match.start()]
        paragraph_start = match.end()

        if match.start() <= floor or _ends_with_heading_line(body, match.start()):
            continue
        if _is_complete_paragraph(paragraph):
            completed_paragraphs.append(match.start())
        else:
            incomplete_paragraphs.append(match.start())

    sentence_boundaries = [
        match.end()
        for match in _SENTENCE_END.finditer(body, start, limit)
        if floor < match.end() < limit and not _ends_with_heading_line(body, match.end())
    ]
    article_boundaries = [
        match.start()
        for match in _ARTICLE_HEAD.finditer(body, start, limit)
        if floor < match.start() and not _ends_with_heading_line(body, match.start())
    ]

    if completed_paragraphs:
        return completed_paragraphs[-1]
    if sentence_boundaries or article_boundaries:
        return max(sentence_boundaries + article_boundaries)
    if incomplete_paragraphs:
        return incomplete_paragraphs[-1]
    return None


def _ends_with_heading_line(body: str, cut: int) -> bool:
    """`cut`에서 자르면 앞 조각의 마지막 비공백 줄이 ATX 헤딩인가."""
    end = cut
    while end > 0 and body[end - 1].isspace():
        end -= 1
    line_start = body.rfind("\n", 0, end) + 1
    return _ATX_HEADING_LINE.match(body, line_start, end) is not None


def _is_complete_paragraph(paragraph: str) -> bool:
    stripped = paragraph.rstrip()
    if not stripped:
        return False
    if stripped[-1] in _PARAGRAPH_TERMINATORS:
        return True

    last_line = stripped.splitlines()[-1]
    return len(last_line) <= 200 and bool(_MARKDOWN_STRUCTURE_LINE.match(last_line))
