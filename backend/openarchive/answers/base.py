"""답변 생성 계약 (ADR-043).

로컬 Ollama와 테스트용 fake만 둔다. 상용 API 프로바이더는 만들지 않는다
(ADR-003·043). 추상 기반 클래스나 레지스트리 대신 Protocol 하나로 충분하다.
"""

from typing import Protocol


class AnswerUnavailable(Exception):
    """모델 호출 실패 — 연결·타임아웃·HTTP 오류·유효한 답변 없음."""


class AnswerProvider(Protocol):
    name: str

    def generate(self, system: str, prompt: str) -> str:
        """답변을 동기로 생성한다. 서비스가 asyncio.to_thread로 호출한다."""
        ...
