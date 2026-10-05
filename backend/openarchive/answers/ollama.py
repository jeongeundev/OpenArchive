"""로컬 Ollama의 비스트리밍 답변 호출 (ADR-043 구현 형태).

표준 라이브러리 HTTP만 사용한다. 추론 출력은 끄고 온도를 0으로 고정한다.
모델 적재는 Ollama가 관리하며 호출 실패는 AnswerUnavailable로 전달한다.
"""

import json
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import Request, urlopen

from openarchive.answers.base import AnswerUnavailable


class OllamaProvider:
    name = "ollama"

    def __init__(self, url: str, model: str, timeout: float):
        self.url = url
        self.model = model
        self.timeout = timeout

    def generate(self, system: str, prompt: str) -> str:
        try:
            request = Request(
                self.url.rstrip("/") + "/api/chat",
                data=json.dumps({
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0},
                }).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
            message = payload.get("message") if isinstance(payload, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Ollama 응답에 유효한 message.content가 없습니다")
            return content.strip()
        except (URLError, OSError, HTTPException, ValueError) as error:
            # TimeoutError・socket.timeout도 OSError에 포함된다. 원인을 유지한다.
            raise AnswerUnavailable(f"Ollama 답변 생성 실패: {error}") from error
