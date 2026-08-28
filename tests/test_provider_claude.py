"""The Claude backend, checked against the real SDK and a mock API endpoint.

These tests exercise the actual `anthropic` package: the request shape it sends,
the streaming path, and the error classes `describe_api_error` branches on. They
catch the kind of break a fake client cannot, such as a parameter the SDK
renamed or refuses.

No network access and no API key are needed; the SDK is pointed at a local
server through ANTHROPIC_BASE_URL.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

anthropic = pytest.importorskip("anthropic")

from vi_ai.config import Config
from vi_ai.providers.base import ProviderUnavailable
from vi_ai.providers.claude import ClaudeProvider, describe_api_error


def sse(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


def stream_body(chunks: list[str], stop_reason: str = "end_turn") -> bytes:
    body = sse("message_start", {
        "type": "message_start",
        "message": {
            "id": "msg_test", "type": "message", "role": "assistant",
            "model": "claude-opus-5", "content": [], "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        },
    })
    body += sse("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    for chunk in chunks:
        body += sse("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": chunk},
        })
    body += sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    body += sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": 5},
    })
    body += sse("message_stop", {"type": "message_stop"})
    return body


class MockAPI:
    """A stand-in for api.anthropic.com that records the requests it receives."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.chunks = ["Hello. ", "World."]
        self.stop_reason = "end_turn"
        self.error_body = {"type": "error", "error": {"type": "api_error",
                                                      "message": "boom"}}
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(length)
                mock.requests.append(json.loads(raw or b"{}"))

                if mock.status != 200:
                    payload = json.dumps(mock.error_body).encode()
                    self.send_response(mock.status)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return

                payload = stream_body(mock.chunks, mock.stop_reason)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
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
def api(monkeypatch):
    mock = MockAPI()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", mock.url)
    yield mock
    mock.close()


@pytest.fixture
def client(api):
    config = Config()
    # One attempt, so a deliberate error surfaces immediately.
    return ClaudeProvider(config.api, "sk-ant-test")


def test_a_streamed_answer_arrives_in_pieces(client, api):
    assert list(client.stream("hi")) == ["Hello. ", "World."]


def test_the_request_carries_the_configured_settings(client, api):
    list(client.stream("hi"))
    body = api.requests[0]
    assert body["model"] == "claude-opus-5"
    assert body["max_tokens"] == 4096
    assert body["stream"] is True
    assert body["output_config"] == {"effort": "medium"}
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert "markdown" in body["system"].lower(), (
        "the system prompt is what keeps answers listenable"
    )


def test_effort_none_omits_the_parameter(api):
    config = Config()
    config.api.effort = "none"
    client = ClaudeProvider(config.api, "sk-ant-test")
    list(client.stream("hi"))
    assert "output_config" not in api.requests[0]


def test_history_is_sent_back_so_claude_remembers(client, api):
    list(client.stream("my name is Sam"))
    list(client.stream("what is my name"))
    assert [m["role"] for m in api.requests[1]["messages"]] == [
        "user", "assistant", "user"
    ]
    assert api.requests[1]["messages"][0]["content"] == "my name is Sam"


def test_history_is_trimmed_and_still_starts_with_a_user_turn(api):
    config = Config()
    config.api.max_history_messages = 2
    client = ClaudeProvider(config.api, "sk-ant-test")
    for _ in range(4):
        list(client.stream("question"))
    sent = api.requests[-1]["messages"]
    assert sent[0]["role"] == "user", "the API rejects a history starting on assistant"
    assert len(sent) <= 3


def test_a_failed_request_leaves_the_history_unchanged(client, api):
    list(client.stream("first"))
    api.status = 500
    with pytest.raises(anthropic.APIStatusError):
        list(client.stream("second"))
    # "second" must not linger as an unanswered user turn.
    assert [m["content"] for m in client.history] == ["first", "Hello. World."]


def test_a_refusal_is_reported_in_words(client, api):
    api.stop_reason = "refusal"
    text = "".join(client.stream("hi"))
    assert "declined" in text


def test_hitting_the_length_limit_is_reported(client, api):
    api.stop_reason = "max_tokens"
    text = "".join(client.stream("hi"))
    assert "cut off" in text


def test_a_missing_key_is_caught_before_any_request(api):
    client = ClaudeProvider(Config().api, "")
    assert "API key" in (client.preflight() or "")
    with pytest.raises(ProviderUnavailable):
        list(client.stream("hi"))
    assert api.requests == []


@pytest.mark.parametrize(
    "status,fragment",
    [
        (401, "not accepted"),
        (403, "permission"),
        (404, "model name"),
        (429, "busy"),
        (500, "status 500"),
    ],
)
def test_http_errors_become_sentences_worth_hearing(api, status, fragment):
    config = Config()
    client = ClaudeProvider(config.api, "sk-ant-test")
    api.status = status
    with pytest.raises(anthropic.APIStatusError) as excinfo:
        list(client.stream("hi"))
    assert fragment in describe_api_error(excinfo.value)


def test_a_connection_failure_is_explained(monkeypatch):
    """The most likely fault on a Pi is the network, so it must be clear."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    config = Config()
    config.timeout_seconds = 2
    client = ClaudeProvider(config.api, "sk-ant-test")
    with pytest.raises(anthropic.APIConnectionError) as excinfo:
        list(client.stream("hi"))
    assert "internet" in describe_api_error(excinfo.value)
