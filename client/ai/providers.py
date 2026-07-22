"""LLM provider registry — the single place a chat-completion provider is defined.

Owns: which providers exist, their endpoint / env-var / default-model
configuration, and which one is active (``LLM_PROVIDER`` in the environment or
.env, defaulting to Groq). Must not own: HTTP (chat_client.py), prompts, or
strategy behaviour (llm_strategy.py).

Swapping providers is therefore zero code: set ``LLM_PROVIDER`` and the
matching API key in .env. Adding a provider that speaks the OpenAI
chat-completions shape is one ProviderSpec entry in PROVIDERS. A provider with
its own wire shape additionally implements ChatClientInterface in its own
module and gets its construction branch in build_chat_client.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from client.ai.chat_client import ChatClientInterface, OpenAiCompatChatClient
from client.ai.env_loader import DEFAULT_ENV_FILE, read_env_value

LLM_PROVIDER_VAR = "LLM_PROVIDER"
DEFAULT_PROVIDER_NAME = "groq"


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the client needs to talk to one hosted LLM provider."""

    name: str
    label: str
    url: str
    api_key_var: str
    model_var: str
    default_model: str


PROVIDERS: Dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        name="groq",
        label="Groq AI",
        url="https://api.groq.com/openai/v1/chat/completions",
        api_key_var="GROQ_API_KEY",
        model_var="GROQ_MODEL",
        default_model="llama-3.3-70b-versatile",
    ),
    "openai": ProviderSpec(
        name="openai",
        label="OpenAI",
        url="https://api.openai.com/v1/chat/completions",
        api_key_var="OPENAI_API_KEY",
        model_var="OPENAI_MODEL",
        default_model="gpt-4o-mini",
    ),
}


def active_provider(env_file: Path = DEFAULT_ENV_FILE) -> ProviderSpec:
    """The provider LLM_PROVIDER names, defaulting to Groq.

    Fails fast on an unknown name — a typo in .env should surface here, not as
    a silently different provider answering the bot's prompts.
    """
    name = read_env_value(LLM_PROVIDER_VAR, env_file) or DEFAULT_PROVIDER_NAME
    spec = PROVIDERS.get(name.lower())
    if spec is None:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown {LLM_PROVIDER_VAR} {name!r}; expected one of: {known}")
    return spec


def load_api_key(spec: ProviderSpec, env_file: Path = DEFAULT_ENV_FILE) -> Optional[str]:
    """The player's API key for *spec*, or None when none is configured."""
    return read_env_value(spec.api_key_var, env_file)


def build_chat_client(env_file: Path = DEFAULT_ENV_FILE) -> Optional[ChatClientInterface]:
    """A ready chat client for the active provider, or None with no API key configured.

    All PROVIDERS entries today are OpenAI-compatible; a provider with its own
    wire shape would branch here to its dedicated ChatClientInterface impl.
    """
    spec = active_provider(env_file)
    api_key = load_api_key(spec, env_file)
    if not api_key:
        return None
    model = read_env_value(spec.model_var, env_file) or spec.default_model
    return OpenAiCompatChatClient(url=spec.url, api_key=api_key, model=model)
