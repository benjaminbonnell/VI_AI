"""The Ollama backend, against a mock Ollama server.

Ollama is the free way to run VI-AI, so the failures worth testing are the
setup ones: the server not running, and the model not pulled. Both must
produce a sentence that says what to do.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vi_ai.config import from_dict
from vi_ai.providers.base import ProviderUnavailable
from vi_ai.providers.ollama import OllamaError, OllamaProvider


class MockOllama:
    """Speaks enough of the Ollama HTTP API to drive the provider."""

    def __init__(self):
        self.requests: list[dict] = []
        self.installed = ["llama3.2:latest"]
        self.chunks = ["Hello", " there", "."]
        self.error: str | None = None
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # /api/tags
                payload = json.dumps(
                    {"models": [{"name": name} for name in mock.installed]}
                ).encode()
                self._send(200, payload, "application/json")

            def do_POST(self):  # /api/chat
                length = int(self.headers.get("content-length", 0))
                mock.requests.append(json.loads(self.rfile.read(length) or b"{}"))

                lines = []
                if mock.error:
                    lines.append(json.dumps({"error": mock.error}))
                else:
                    for chunk in mock.chunks:
                        lines.append(json.dumps({
                            "message": {"role": "assistant", "content": chunk},
                            "done": False,
                        }))
                    lines.append(json.dumps({
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                    }))
                payload = ("\n".join(lines) + "\n").encode()
                self._send(200, payload, "application/x-ndjson")

            def _send(self, status, payload, content_type):
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama():
    mock = MockOllama()
    yield mock
    mock.close()


def make_provider(ollama, model="llama3.2"):
    config = from_dict({
        "api": {"provider": "ollama"},
        "ollama": {"host": ollama.url, "model": model},
    })
    return OllamaProvider(config.api, config.ollama)


def test_a_streamed_answer_arrives_in_pieces(ollama):
    provider = make_provider(ollama)
    assert list(provider.stream("hi")) == ["Hello", " there", "."]


def test_the_request_carries_the_model_and_the_system_prompt(ollama):
    provider = make_provider(ollama)
    list(provider.stream("hi"))
    body = ollama.requests[0]
    assert body["model"] == "llama3.2"
    assert body["stream"] is True
    assert body["messages"][0]["role"] == "system"
    assert "markdown" in body["messages"][0]["content"].lower()
    assert body["messages"][-1] == {"role": "user", "content": "hi"}


def test_history_is_sent_back_so_the_model_remembers(ollama):
    provider = make_provider(ollama)
    list(provider.stream("my name is Sam"))
    list(provider.stream("what is my name"))
    roles = [m["role"] for m in ollama.requests[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_preflight_passes_when_the_model_is_installed(ollama):
    assert make_provider(ollama).preflight() is None


def test_a_bare_model_name_matches_the_latest_tag(ollama):
    """`ollama pull llama3.2` installs "llama3.2:latest"."""
    ollama.installed = ["llama3.2:latest"]
    assert make_provider(ollama, "llama3.2").preflight() is None


def test_preflight_says_how_to_install_a_missing_model(ollama):
    ollama.installed = ["qwen2.5:0.5b"]
    problem = make_provider(ollama, "llama3.2").preflight()
    assert "ollama pull llama3.2" in problem
    assert "qwen2.5:0.5b" in problem, "list what is available, to save a guess"


def test_preflight_says_how_to_start_a_stopped_server():
    """The most likely first-run problem, so the wording has to be actionable."""
    config = from_dict({"ollama": {"host": "http://127.0.0.1:1"}})
    problem = OllamaProvider(config.api, config.ollama).preflight()
    assert "ollama serve" in problem


def test_a_server_error_becomes_a_readable_sentence(ollama):
    provider = make_provider(ollama)
    ollama.error = 'model "llama3.2" not found, try pulling it first'
    with pytest.raises(OllamaError) as excinfo:
        list(provider.stream("hi"))
    assert "ollama pull" in provider.describe_error(excinfo.value)


def test_an_unreachable_server_is_explained_during_a_request():
    config = from_dict({"ollama": {"host": "http://127.0.0.1:1"}})
    provider = OllamaProvider(config.api, config.ollama)
    with pytest.raises(Exception) as excinfo:
        list(provider.stream("hi"))
    assert "ollama serve" in provider.describe_error(excinfo.value)


def test_a_failed_request_leaves_the_history_unchanged(ollama):
    provider = make_provider(ollama)
    list(provider.stream("first"))
    ollama.error = "boom"
    with pytest.raises(OllamaError):
        list(provider.stream("second"))
    assert [m["content"] for m in provider.history] == ["first", "Hello there."]


def test_unparsable_lines_are_skipped_rather_than_crashing(ollama):
    """A partial line must not lose the whole answer."""
    provider = make_provider(ollama)
    assert list(provider.stream("hi")) == ["Hello", " there", "."]
    assert isinstance(ProviderUnavailable("x"), Exception)
