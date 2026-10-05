"""모델 없이 인용 계약을 검증하는 결정론적 답변 프로바이더.

프롬프트의 근거 라벨을 그대로 인용해 서비스의 cited 판정을 검증한다.
실제 답변 품질을 흉내 내지 않는다.
"""

import re


class FakeAnswerProvider:
    name = "fake"

    def generate(self, system: str, prompt: str) -> str:
        labels = dict.fromkeys(re.findall(r"\[(\d+)\]", prompt))
        if not labels:
            return "근거 문서에서 찾을 수 없습니다."
        return "근거 문서를 참고하세요. " + " ".join(f"[{label}]" for label in labels)
