"""답변 프로바이더 선택 — 기본 꺼짐이 목적이다 (ADR-043 결정 2).

예열하지 않는다 — Ollama가 모델 적재를 스스로 관리하고, 꺼져 있을 수 있는
외부 서버를 기동 경로에 넣지 않는다. 임베딩처럼 Protocol과 match만 둔다.
"""

from openarchive.answers.base import AnswerProvider, AnswerUnavailable
from openarchive.answers.fake import FakeAnswerProvider
from openarchive.answers.ollama import OllamaProvider
from openarchive.config import get_settings

__all__ = [
    "AnswerProvider", "AnswerUnavailable", "FakeAnswerProvider", "OllamaProvider",
    "get_answer_provider",
]


def get_answer_provider(name: str | None = None) -> AnswerProvider | None:
    """이름이 없으면 ANSWER_PROVIDER 설정을 따른다. 인스턴스는 캐시하지 않는다."""
    settings = get_settings()
    resolved = name if name is not None else settings.answer_provider
    match resolved:
        case "off":
            return None
        case "fake":
            return FakeAnswerProvider()
        case "ollama":
            return OllamaProvider(
                settings.ollama_url, settings.answer_model, settings.answer_timeout_seconds,
            )
        case _:
            raise ValueError(f"알 수 없는 답변 프로바이더입니다: {resolved!r} (off | ollama | fake)")
