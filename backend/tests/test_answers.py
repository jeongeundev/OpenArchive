"""답변 프로바이더 계약 — 모델 없이 실제 HTTP 경로를 검증한다."""

import json
import re
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openarchive.answers import AnswerUnavailable, get_answer_provider
from openarchive.answers.fake import FakeAnswerProvider
from openarchive.answers.ollama import OllamaProvider
from openarchive.config import get_settings


@contextmanager
def chat_server(body=None, *, status=200, delay=0):
    if body is None:
        body = json.dumps({"message": {"content": "  답 [1]  "}}).encode()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            time.sleep(delay)
            self.send_response(status)
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass  # 타임아웃 테스트의 클라이언트는 이미 연결을 닫았다.

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_factory_selects_only_supported_providers(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:12345")
    monkeypatch.setenv("ANSWER_MODEL", "test-model")
    monkeypatch.setenv("ANSWER_TIMEOUT_SECONDS", "2.5")
    assert get_answer_provider("off") is None
    assert isinstance(get_answer_provider("fake"), FakeAnswerProvider)
    provider = get_answer_provider("ollama")
    assert isinstance(provider, OllamaProvider)
    assert (provider.url, provider.model, provider.timeout) == (
        "http://127.0.0.1:12345", "test-model", 2.5,
    )
    with pytest.raises(ValueError, match="gpt"):
        get_answer_provider("gpt")


@pytest.mark.parametrize("name", ["off", "fake", "ollama"])
def test_factory_uses_settings_when_name_is_omitted(monkeypatch, name):
    monkeypatch.setenv("ANSWER_PROVIDER", name)
    get_settings.cache_clear()
    provider = get_answer_provider()
    assert (provider.name if provider else "off") == name


def test_fake_is_deterministic_and_cites_prompt_labels():
    provider = FakeAnswerProvider()
    prompt = "[1] 첫 근거 [2] 둘째 근거 [12] 다른 근거 [1] 반복"
    answer = provider.generate("지시 [99]", prompt)
    assert answer == provider.generate("지시 [99]", prompt)
    assert set(re.findall(r"\[(\d+)\]", answer)) == {"1", "2", "12"}
    assert provider.name == "fake"


def test_fake_without_labels_reports_missing_evidence():
    answer = FakeAnswerProvider().generate("지시", "라벨 없는 질문")
    assert "근거 문서에서 찾을 수 없습니다" in answer
    assert not re.findall(r"\[(\d+)\]", answer)


@pytest.mark.parametrize("suffix", ["", "/"])
def test_ollama_posts_chat_contract_and_strips_response(suffix):
    with chat_server() as (url, requests):
        provider = OllamaProvider(url + suffix, "test-model", 2)
        assert provider.generate("시스템", "질문") == "답 [1]"
        assert provider.name == "ollama"
        assert requests == [("/api/chat", {
            "model": "test-model",
            "messages": [
                {"role": "system", "content": "시스템"},
                {"role": "user", "content": "질문"},
            ],
            "stream": False, "think": False, "options": {"temperature": 0},
        })]


@pytest.mark.parametrize("body,status", [
    (b"server failed", 500),
    (b"not json", 200),
    (b"{}", 200),
    (b'{"message": {}}', 200),
    (b'{"message": {"content": "  "}}', 200),
    (b'{"message": {"content": null}}', 200),
    (b'{"message": null}', 200),
    (b"[]", 200),
])
def test_ollama_bad_responses_are_unavailable(body, status):
    with chat_server(body, status=status) as (url, _):
        with pytest.raises(AnswerUnavailable) as caught:
            OllamaProvider(url, "model", 2).generate("system", "prompt")
        assert str(caught.value)
        assert caught.value.__cause__ is not None


def test_ollama_connection_refused_is_unavailable():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(AnswerUnavailable) as caught:
        OllamaProvider(f"http://127.0.0.1:{port}", "model", 0.2).generate("s", "p")
    assert caught.value.__cause__ is not None


def test_ollama_timeout_is_unavailable():
    with chat_server(delay=0.5) as (url, _):
        with pytest.raises(AnswerUnavailable) as caught:
            OllamaProvider(url, "model", 0.2).generate("s", "p")
        assert caught.value.__cause__ is not None
