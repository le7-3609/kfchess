"""Unit tests for OpenAiCompatChatClient payload/response handling and the .env loader.

The transport is always a fake; no test here opens a socket.
"""

import pytest

from client.ai.chat_client import ChatClientError, OpenAiCompatChatClient
from client.ai.env_loader import parse_env_file, read_env_value


class _RecordingTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.url = None
        self.headers = None
        self.payload = None

    def post_json(self, url, headers, payload, timeout_s):
        self.url = url
        self.headers = headers
        self.payload = payload
        return self.response


def _reply_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def _make_client(transport, model: str = "test-model") -> OpenAiCompatChatClient:
    return OpenAiCompatChatClient(
        url="https://example.test/v1/chat/completions",
        api_key="sk-test",
        model=model,
        transport=transport,
    )


class TestOpenAiCompatChatClient:
    def test_sends_model_messages_and_bearer_auth_to_the_given_url(self):
        transport = _RecordingTransport(_reply_response("3"))
        client = _make_client(transport)

        reply = client.complete("system text", "user text")

        assert reply == "3"
        assert transport.url == "https://example.test/v1/chat/completions"
        assert transport.payload["model"] == "test-model"
        assert transport.payload["messages"] == [
            {"role": "system", "content": "system text"},
            {"role": "user", "content": "user text"},
        ]
        assert transport.headers["Authorization"] == "Bearer sk-test"

    def test_a_malformed_response_raises(self):
        transport = _RecordingTransport({"error": "nope"})
        client = _make_client(transport)

        with pytest.raises(ChatClientError):
            client.complete("s", "u")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"url": "", "api_key": "sk-test", "model": "m"},
            {"url": "https://example.test", "api_key": "", "model": "m"},
            {"url": "https://example.test", "api_key": "sk-test", "model": ""},
        ],
    )
    def test_missing_construction_arguments_are_rejected(self, kwargs):
        with pytest.raises(ValueError):
            OpenAiCompatChatClient(**kwargs)


class TestEnvLoader:
    def test_parses_values_and_skips_comments_and_blanks(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# comment\n\nSOME_KEY=sk-abc\nSOME_MODEL='quoted-model'\nnot a pair\n",
            encoding="utf-8",
        )

        values = parse_env_file(env_file)

        assert values == {"SOME_KEY": "sk-abc", "SOME_MODEL": "quoted-model"}

    def test_a_missing_file_reads_as_no_values(self, tmp_path):
        assert parse_env_file(tmp_path / "absent.env") == {}

    def test_process_environment_wins_over_the_file(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("SOME_KEY=from-file\n", encoding="utf-8")
        monkeypatch.setenv("SOME_KEY", "from-process")

        assert read_env_value("SOME_KEY", env_file) == "from-process"

    def test_falls_back_to_the_file_when_unset(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("SOME_KEY=from-file\n", encoding="utf-8")
        monkeypatch.delenv("SOME_KEY", raising=False)

        assert read_env_value("SOME_KEY", env_file) == "from-file"

    def test_an_empty_value_reads_as_unconfigured(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("SOME_KEY=\n", encoding="utf-8")
        monkeypatch.delenv("SOME_KEY", raising=False)

        assert read_env_value("SOME_KEY", env_file) is None
