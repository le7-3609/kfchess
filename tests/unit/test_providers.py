"""Unit tests for the LLM provider registry — provider swap is config, not code.

Every test pins environment variables through monkeypatch so a developer's real
.env or shell exports never leak in; no test opens a socket.
"""

import pytest

from client.ai.chat_client import OpenAiCompatChatClient
from client.ai.providers import (
    LLM_PROVIDER_VAR,
    PROVIDERS,
    active_provider,
    build_chat_client,
    load_api_key,
)

_ALL_ENV_VARS = [LLM_PROVIDER_VAR] + [
    var for spec in PROVIDERS.values() for var in (spec.api_key_var, spec.model_var)
]


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """An isolated .env path with every provider variable cleared from the process."""
    for var in _ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return tmp_path / ".env"


class TestProviderSelection:
    def test_defaults_to_groq_with_nothing_configured(self, env_file):
        assert active_provider(env_file).name == "groq"

    def test_llm_provider_selects_another_registry_entry(self, env_file):
        env_file.write_text("LLM_PROVIDER=openai\n", encoding="utf-8")

        spec = active_provider(env_file)

        assert spec.name == "openai"
        assert spec.api_key_var == "OPENAI_API_KEY"

    def test_an_unknown_provider_name_fails_fast(self, env_file):
        env_file.write_text("LLM_PROVIDER=skynet\n", encoding="utf-8")

        with pytest.raises(ValueError, match="skynet"):
            active_provider(env_file)

    def test_every_registry_entry_is_internally_consistent(self):
        for name, spec in PROVIDERS.items():
            assert spec.name == name
            assert spec.url.startswith("https://")
            assert spec.label and spec.api_key_var and spec.model_var and spec.default_model


class TestClientComposition:
    def test_no_api_key_means_no_client(self, env_file):
        assert build_chat_client(env_file) is None

    def test_a_configured_key_builds_a_client_for_the_active_provider(self, env_file):
        env_file.write_text(
            "LLM_PROVIDER=openai\nOPENAI_API_KEY=sk-test\n", encoding="utf-8"
        )

        client = build_chat_client(env_file)

        assert isinstance(client, OpenAiCompatChatClient)
        assert client._url == PROVIDERS["openai"].url
        assert client._model == PROVIDERS["openai"].default_model

    def test_the_model_override_variable_wins_over_the_default(self, env_file):
        env_file.write_text(
            "GROQ_API_KEY=gsk-test\nGROQ_MODEL=custom-model\n", encoding="utf-8"
        )

        client = build_chat_client(env_file)

        assert client._model == "custom-model"

    def test_load_api_key_reads_the_active_providers_variable(self, env_file):
        env_file.write_text("GROQ_API_KEY=gsk-test\n", encoding="utf-8")

        spec = active_provider(env_file)

        assert load_api_key(spec, env_file) == "gsk-test"
