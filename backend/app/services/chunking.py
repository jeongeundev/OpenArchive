"""문서 본문을 임베딩 단위로 자르는 순수 함수 (ARCHITECTURE.md "청킹").

DB도 모델도 파일시스템도 시간도 건드리지 않는다. 워커의 나머지 부분(트랜잭션·잠금·
재시도)과 분리해 두어야 청킹 규칙만 밀리초 단위로 검증할 수 있다.

길이 단위는 **문자 수**다. 토크나이저로 세면 모델 의존성이 생겨 순수 함수가 아니게
되고, BGE-M3의 8192 토큰 한도에 1,000자는 충분히 여유가 있다 (ADR-003).
"""

import re

# 빈 줄 = 문단 경계. 줄 끝에 공백이 남아 있어도 경계로 본다 — 편집기나 PDF 파서를
# 거친 텍스트에서 흔하다. `[^\S\n]`은 개행을 뺀 공백이다.
_PARAGRAPH_BREAK = re.compile(r"\n[^\S\n]*\n")


def chunk_text(text: str, *, max_chars: int = 1000, overlap: int = 150) -> list[str]:
    """문서 본문을 임베딩 단위로 자른다. 문단 경계를 우선 존중한다.

    `max_chars` 폭의 창을 앞으로 밀며 조각을 만든다. 창 안에 문단 경계가 있으면
    거기서 끊고, 없으면 창 끝에서 강제로 자른다. 다음 창은 직전 조각의 끝에서
    `overlap`만큼 되돌아간 자리에서 시작하므로 인접 조각이 텍스트를 공유한다.
    오버랩이 창 **안에** 들어 있으므로, 덧붙인 부분까지 합쳐 `max_chars`를 넘지 않는다.

    같은 입력에는 항상 같은 출력을 낸다. 재임베딩이 멱등하려면 이 성질이 필요하다 —
    같은 본문을 다시 처리했는데 청크 경계가 달라지면 검색 결과가 이유 없이 흔들린다.

    빈 문자열이나 공백뿐인 본문은 빈 리스트를 반환한다.

    Raises:
        ValueError: `max_chars`가 1 미만이거나, `overlap`이 음수 또는 `max_chars` 이상일 때.
            `overlap >= max_chars`면 창의 전진 폭이 0 이하가 되어 분할이 끝나지 않는다.
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

    chunks: list[str] = []
    start = 0
    while True:
        limit = start + max_chars
        if limit >= len(body):
            end = len(body)
        else:
            cut = _paragraph_cut(body, start, limit, floor=start + overlap)
            end = cut if cut is not None else limit

        piece = body[start:end].strip()
        if piece:
            chunks.append(piece)

        if end >= len(body):
            return chunks
        start = end - overlap


def _paragraph_cut(body: str, start: int, limit: int, floor: int) -> int | None:
    """`[start, limit)` 안의 마지막 문단 경계 위치. 쓸 만한 경계가 없으면 None.

    마지막 경계를 고르는 것은 창을 최대한 채우기 위해서다. 짧은 문단 여러 개는 한
    청크로 묶인다.

    `floor`(= start + overlap) 이하의 경계를 버리는 이유는 전진 보장이다. 다음 창은
    `cut - overlap`에서 시작하므로, 경계가 그보다 앞이면 창이 제자리이거나 뒤로
    물러난다. 버려진 경계는 다음 조각 안쪽에 그대로 남으므로 내용은 잃지 않는다.
    """
    cut = None
    for match in _PARAGRAPH_BREAK.finditer(body, start, limit):
        if match.start() > floor:
            cut = match.start()
    return cut
