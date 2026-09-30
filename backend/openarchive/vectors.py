"""pgvector 입력 리터럴 변환.

워커(청크 임베딩 삽입)와 검색 서비스(질의 벡터 바인딩)가 같은 변환을 쓴다. 두 곳에
따로 두었더니 한쪽은 repr, 다른 쪽은 str로 갈라져 있었다 — 지금은 같은 결과지만
포맷이 어긋나면 조용히 정밀도만 달라지므로 한 곳에 둔다.

읽기용 파싱은 없다. 벡터를 파이썬으로 되읽는 경로가 없어 어댑터 패키지가 필요 없다.
"""


def to_pgvector_literal(vector: list[float]) -> str:
    return "[" + ",".join(map(repr, vector)) + "]"
