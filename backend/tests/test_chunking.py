"""청킹 규칙 (ARCHITECTURE.md "청킹 (services/chunking.py)", ADR-003).

DB도 임베딩 모델도 필요 없다 — 순수 함수 하나를 밀리초 단위로 두들긴다. 워커
테스트는 느리고 DB를 요구하므로, 이만큼의 경계 조건을 거기서 확인할 수 없다.

여기서 고정하는 것은 구현이 아니라 **불변식**이다. 분할 지점을 어떻게 고르든 아래는
깨지면 안 된다.

1. 모든 청크가 `max_chars` 이하 — **오버랩으로 덧붙인 부분까지 포함해서**
2. 내용 누락 없음 — 임베딩되지 않은 본문 조각은 영원히 검색되지 않는다
3. 인접 청크가 겹치는 텍스트를 공유 — 경계에서 잘린 문장의 의미 손실을 완화한다
4. 결정론 — 재임베딩의 멱등성이 여기에 걸려 있다
"""

from itertools import pairwise

import pytest

from openarchive.services.chunking import chunk_text

# 문단 경계(빈 줄)가 하나도 없는 본문. 강제 분할 경로를 탄다.
LONG_SENTENCE = "임베딩 잡은 트리거가 만들고 워커는 그것을 집어간다. "

# 공백도 반복 주기도 없는 본문. 겹치는 구간을 문자 단위로 정확히 비교할 수 있다 —
# 주기적인 문자열을 쓰면 우연히 더 긴 구간이 일치해 비교가 무의미해진다.
DIGITS = "".join(f"{i:04d}" for i in range(700))  # 2,800자

# 문단 끝을 표식으로 남긴 본문. 청크가 문단 중간에서 끊겼는지 눈으로 판정할 수 있다.
MARKED_PARAGRAPHS = [
    f"{i}번 문단이다. " + "정합성은 DB가 보장한다. " * 3 + f"[{i}번 끝]" for i in range(1, 7)
]
MARKED_DOC = "\n\n".join(MARKED_PARAGRAPHS)

# 실제 대상 문서에 가까운 한국어. 공백 기준 단어 분할 가정이 한국어에서 다르게
# 동작할 수 있어 별도로 확인한다.
KOREAN_PARAGRAPHS = [
    (
        "OpenArchive는 문서를 업로드하면 DB 트리거가 임베딩 잡을 만든다. "
        "애플리케이션 코드에는 임베딩 파이프라인을 조율하는 부분이 없다."
    ),
    (
        "워커는 5초 주기 폴링을 주 경로로 삼아 잡을 드레인한다. "
        "LISTEN/NOTIFY는 지연을 줄이는 최적화이며, 동작하지 않아도 파이프라인은 그대로 돈다."
    ),
    (
        "검색은 정형 필터와 벡터 유사도를 단일 SQL로 결합한다. "
        "태그·유형·권한 술어가 벡터 정렬과 같은 쿼리 안에 들어간다."
    ),
    (
        "재임베딩 중에는 이전 버전 청크가 검색된다. "
        "검색 공백이 없고, 그때 나오는 것은 낡았을지언정 일관된 한 버전이다."
    ),
]
KOREAN_DOC = "\n\n".join(KOREAN_PARAGRAPHS)

BOUNDARY_CASES = [
    "검토의견을 받아야 한다.제6조(채용방법) ① 채용 절차를 정한다. " * 12,
    "검토의\n\n-439-\n견을 받아 첨부하여야 한다.  ② 다음 절차를 따른다. " * 8,
    "도입 문단을 마친다.\n\n### 절 제목\n\n" + "긴 설명을 이어 간다. " * 30,
    "위임할 수 있으며 그 범위는 별표 1과 같다 제6조(채용방법) ① 기준은 별표 2에 따른다 " * 8,
    "도입이 마침표 없이 이어지고 " * 3 + "\n\n### 왜 그런가?\n\n" + "긴 설명을 이어 간다. " * 30,
]


def nonspace(text: str) -> str:
    """공백을 모두 제거한 문자열. 내용 비교에서 줄바꿈·들여쓰기 차이를 지운다."""
    return "".join(text.split())


def is_subsequence(needle: str, haystack: str) -> bool:
    """`needle`의 모든 문자가 `haystack`에 **순서대로** 나타나는가.

    부분 문자열이 아니라 부분 수열을 보는 이유: 오버랩 때문에 청크를 이어붙이면
    겹치는 구간이 두 번 등장한다. 원문 "ABCDEF"가 "ABCD"+"CDEF"로 잘리면 이어붙인
    결과는 "ABCDCDEF"라 원문이 부분 문자열이 아니다. 누락을 잡아내는 데는 순서를
    지킨 포함 관계로 충분하다.
    """
    remaining = iter(haystack)
    return all(char in remaining for char in needle)


def shared_boundary(left: str, right: str) -> str:
    """`left`의 접미사이면서 `right`의 접두사인 가장 긴 문자열. 없으면 빈 문자열."""
    for size in range(min(len(left), len(right)), 0, -1):
        if left[-size:] == right[:size]:
            return left[-size:]
    return ""


def test_short_text_becomes_one_chunk_unchanged():
    text = "OpenArchive는 문서의 추출 텍스트를 버전 단위로 관리한다."

    assert chunk_text(text) == [text]


def test_surrounding_whitespace_is_trimmed():
    """청크는 앞뒤 공백이 정리된 상태로 나온다 (불변식 6)."""
    assert chunk_text("\n\n  본문 한 줄.  \n\t") == ["본문 한 줄."]


@pytest.mark.parametrize("blank", ["", "   ", "\n\n\n", " \t\r\n \f"])
def test_blank_text_yields_no_chunks(blank: str):
    """DB의 CHECK 제약이 이미 빈 본문을 막지만, 함수 자체가 안전해야 한다."""
    assert chunk_text(blank) == []


def test_a_long_paragraph_is_split_within_the_size_limit():
    text = LONG_SENTENCE * 120  # 빈 줄이 없으므로 문단 경계를 찾지 못한다

    chunks = chunk_text(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)
    # 한 글자씩 잘라도 위 두 조건은 통과한다. 창을 실제로 채우는지 함께 본다.
    assert max(len(chunk) for chunk in chunks) > 900


def test_adjacent_chunks_share_the_overlap_region():
    """"겹칠 것이다"가 아니라 겹치는 구간을 문자 단위로 비교한다 (불변식 3)."""
    chunks = chunk_text(DIGITS, max_chars=1000, overlap=150)

    assert len(chunks) > 1
    for left, right in pairwise(chunks):
        assert left[-150:] == right[:150]
        # 겹치기만 하고 전진하지 않으면 같은 내용이 무한히 반복된다.
        assert len(right) > 150


def test_no_content_is_dropped():
    """원문의 공백 아닌 모든 문자가 청크 어딘가에 순서대로 남는다 (불변식 2)."""
    chunks = chunk_text(KOREAN_DOC, max_chars=120, overlap=30)

    assert is_subsequence(nonspace(KOREAN_DOC), nonspace("".join(chunks)))


def test_paragraphs_are_not_cut_in_the_middle():
    """문단 경계가 창 안에 있으면 거기서 끊는다 (불변식: 분할 우선순위).

    청크의 **시작**은 오버랩 때문에 문단 중간일 수 있다. 여기서 보는 것은 **끝**이다 —
    끝이 문단 중간이면 문장이 잘려 임베딩 품질이 떨어진다.
    """
    chunks = chunk_text(MARKED_DOC, max_chars=200, overlap=50)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.endswith("번 끝]"), f"문단 중간에서 끊겼다: …{chunk[-40:]!r}"


def test_paragraphs_stay_whole_without_overlap():
    """오버랩이 없으면 청크는 온전한 문단들의 묶음이 된다 — 누락도 중복도 없다."""
    chunks = chunk_text(MARKED_DOC, max_chars=200, overlap=0)

    assert [part for chunk in chunks for part in chunk.split("\n\n")] == MARKED_PARAGRAPHS
    assert nonspace("".join(chunks)) == nonspace(MARKED_DOC)


def test_chunking_is_deterministic():
    """같은 입력에 같은 출력. 재임베딩이 멱등하려면 청크 경계가 흔들리면 안 된다."""
    first = chunk_text(KOREAN_DOC, max_chars=150, overlap=40)
    second = chunk_text(KOREAN_DOC, max_chars=150, overlap=40)

    assert first == second
    assert len(first) > 1


def test_small_custom_parameters_keep_the_invariants():
    chunks = chunk_text(KOREAN_DOC, max_chars=50, overlap=10)

    assert all(len(chunk) <= 50 for chunk in chunks)
    assert all(chunk == chunk.strip() and chunk for chunk in chunks)
    assert is_subsequence(nonspace(KOREAN_DOC), nonspace("".join(chunks)))
    for left, right in pairwise(chunks):
        assert shared_boundary(left, right), f"겹치는 구간이 없다: {left[-20:]!r} / {right[:20]!r}"


def test_zero_overlap_reproduces_the_text_exactly():
    chunks = chunk_text(KOREAN_DOC, max_chars=80, overlap=0)

    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert nonspace("".join(chunks)) == nonspace(KOREAN_DOC)


@pytest.mark.parametrize(
    ("max_chars", "overlap"),
    [
        (100, 100),  # 전진 폭 0 — 창이 제자리에 멈춘다
        (100, 150),  # 전진 폭 음수 — 창이 뒤로 물러난다
        (0, 0),
        (100, -1),
    ],
)
def test_parameters_that_would_never_terminate_are_rejected(max_chars: int, overlap: int):
    """`overlap >= max_chars`면 창이 전진하지 못해 슬라이딩이 끝나지 않는다.

    조용히 보정하지 않고 예외로 막는다. 잘못된 설정으로 워커가 멈추는 것보다
    호출 시점에 터지는 편이 낫다.
    """
    with pytest.raises(ValueError):
        chunk_text(KOREAN_DOC, max_chars=max_chars, overlap=overlap)


def test_korean_document_keeps_every_invariant():
    """실제 대상 문서는 한국어다. 위 불변식을 한국어 본문에서 한 번에 확인한다."""
    chunks = chunk_text(KOREAN_DOC, max_chars=100, overlap=25)

    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)  # 1. 길이
    assert is_subsequence(nonspace(KOREAN_DOC), nonspace("".join(chunks)))  # 2. 누락 없음
    for left, right in pairwise(chunks):  # 3. 오버랩
        assert shared_boundary(left, right)
    assert chunks == chunk_text(KOREAN_DOC, max_chars=100, overlap=25)  # 4. 결정론


def test_pdf_text_without_newlines_is_cut_at_a_sentence_end_or_article_head():
    text = (
        "채용에 대한 검토의견을 충분히 받아야 한다."
        "제6조(채용방법) ① 공개채용 절차와 심사 기준을 정한다. "
    ) * 24

    chunks = chunk_text(text)

    assert len(chunks) > 1
    assert all(chunk.endswith("다.") for chunk in chunks[:-1])
    assert all(not chunk.endswith("에 대한 검토의") for chunk in chunks)


def test_an_article_head_is_a_boundary_even_without_a_sentence_end_before_it():
    """pypdf 텍스트에서 조문 머리 앞 문장의 종결 부호가 빠진 경우. 문장 끝 후보가 하나도
    없으므로 `제6조(` 앞에서 자르는 것은 조문 머리 규칙만이 만들 수 있다."""
    text = (
        "① 임용권자는 소속 직원의 임용을 위임할 수 있으며 그 범위는 별표 1과 같다 "
        "제6조(채용방법) ① 공개채용 절차와 심사 기준은 별표 2에 따른다 "
    ) * 24

    chunks = chunk_text(text)

    assert len(chunks) > 1
    assert all(chunk.endswith("별표 1과 같다") for chunk in chunks[:-1])
    assert all("제6조(" in chunk for chunk in chunks[1:])


def test_a_paragraph_break_in_the_middle_of_a_sentence_loses_to_a_later_sentence_end():
    prefix = "앞선 규정을 충분히 설명한다. " * 3
    text = (
        prefix
        + "담당자는 검토의\n\n-439-\n견을 받아 첨부하여야 한다.  ② 후속 절차를 따른다."
        + " 뒤이어 세부 절차와 서식을 정한다." * 3
    )
    assert len(text) > 105  # 한 창에 다 들어가면 경계 선택이 돌지 않는다

    first = chunk_text(text, max_chars=105, overlap=20)[0]

    assert "-439-" in first
    assert first.endswith("절차를 따른다.")


def test_a_sentence_complete_paragraph_break_still_wins_over_later_sentence_ends():
    paragraph = "첫 문단의 규칙과 적용 범위를 충분히 설명하고 여기에서 마친다."
    text = paragraph + "\n\n" + "뒤 문단에도 문장 끝이 있다. " * 8

    first = chunk_text(text, max_chars=100, overlap=20)[0]

    assert first == paragraph


@pytest.mark.parametrize(
    "heading_block",
    [
        "### 절 제목",
        "## 상위\n\n### 하위",
    ],
)
def test_a_heading_is_not_left_at_the_tail_of_a_chunk(heading_block: str):
    intro = "도입 설명을 충분히 이어 간다. " * 3
    text = intro + "\n\n" + heading_block + "\n\n" + "본문 설명을 길게 이어 간다. " * 8

    chunks = chunk_text(text, max_chars=105, overlap=20)

    assert len(chunks) > 1
    assert all(not chunk.rstrip().splitlines()[-1].lstrip().startswith("#") for chunk in chunks)


@pytest.mark.parametrize(
    "heading_block",
    [
        "### 절 제목",
        "### 왜 그런가?",  # 헤딩 끝 `?`가 문장 끝 후보로 잡히면 헤딩이 꼬리에 남는다
        "### 결론. 그리고 시작",  # 헤딩 안의 `. `도 마찬가지다
        "문장이 마침표 없이 계속되고\n## 절 제목",  # 헤딩으로 끝나는 혼합 문단
    ],
)
def test_a_heading_line_is_never_a_chunk_tail_through_any_boundary_grade(heading_block: str):
    """앞 문단이 문장 중간이고 뒤 문단의 첫 문장이 창을 넘어야 헤딩 자리의 후보가 이긴다."""
    intro = "도입 설명이 마침표 없이 이어지고 " * 3
    body = "본문 첫 문장은 창이 끝날 때까지 마침표 없이 계속 이어지는 긴 설명이다 " * 2
    text = intro + "\n\n" + heading_block + "\n\n" + body + "그리고 짧게 마친다. " * 6

    chunks = chunk_text(text, max_chars=105, overlap=20)

    assert len(chunks) > 1
    assert all(not chunk.rstrip().splitlines()[-1].lstrip().startswith("#") for chunk in chunks)
    assert chunks[0].endswith("이어지고")


def test_heading_pullback_never_violates_the_overlap_floor():
    text = (
        "오버랩 영역의 짧은 도입.\n\n### 절 제목을 길게 늘여 오버랩 밖으로 보낸다\n\n```\n"
        + "code_without_sentence_boundary = value\n" * 8
        + "```"
    )
    assert text.index("\n\n```") > 30  # 헤딩 뒤 빈 줄은 floor 밖, 헤딩 앞 빈 줄(14)은 floor 안

    chunks = chunk_text(text, max_chars=90, overlap=30)

    assert len(chunks) > 1
    assert all(len(chunk) > 30 for chunk in chunks[1:])
    assert all(not chunk.rstrip().splitlines()[-1].lstrip().startswith("#") for chunk in chunks)


def test_list_markers_and_dates_are_not_sentence_ends():
    text = (
        "  1. 국가공무원법에 따른 채용 기준을 준수한다. 적용 일자는 제정 2021. 12. 28. "
        "이후 시행하는 절차와 세부 기준은 별도 지침으로 정하는 바에 따르되 다음 절차도 동일하게 처리해요."
    )
    assert text.index("28.") > text.index("준수한다.")  # 날짜가 뒤에 있어야 "뒤쪽 후보 우선"과 구분된다

    chunks = chunk_text(text, max_chars=80, overlap=20)

    assert chunks[0].endswith("기준을 준수한다.")
    assert not chunks[0].endswith(("1.", "2021.", "12.", "28."))
    assert chunks[-1].endswith("처리해요.")


@pytest.mark.parametrize("structural_line", ["- 항목", "| 표 |", "> 인용", "```", "---"])
def test_structural_markdown_lines_count_as_complete_paragraphs(structural_line: str):
    text = structural_line + "\n\n" + "뒤 문단에는 문장 끝이 여러 번 있다. " * 8

    first = chunk_text(text, max_chars=90, overlap=0)[0]

    assert first == structural_line


@pytest.mark.parametrize("text", BOUNDARY_CASES)
def test_new_boundary_cases_keep_every_invariant(text: str):
    max_chars = 120
    overlap = 30
    chunks = chunk_text(text, max_chars=max_chars, overlap=overlap)

    assert all(len(chunk) <= max_chars for chunk in chunks)
    assert is_subsequence(nonspace(text), nonspace("".join(chunks)))
    for left, right in pairwise(chunks):
        assert shared_boundary(left, right)
    assert chunks == chunk_text(text, max_chars=max_chars, overlap=overlap)
