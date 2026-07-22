"""Provider-agnostic chat-completion client (client layer).

Owns: the contract every LLM chat client fulfils (ChatClientInterface), the
OpenAI-compatible request/response shape most hosted providers speak (Groq,
OpenAI, Mistral, ...), and the HTTP transport that carries it. Must not own:
prompts, move parsing, provider selection (providers.py), or any game
knowledge — callers hand in finished message text and get the model's reply
text back.

The transport is injectable so tests exercise payload building and response
parsing against a fake; nothing in the test suite performs a real network call.
"""

import json
import urllib.error
import urllib.request
from typing import Dict, Optional, Protocol

DEFAULT_TIMEOUT_S = 10.0
# The reply the strategy wants is a single move index, so a tight completion
# budget keeps responses fast and stops the model narrating its plans.
MAX_COMPLETION_TOKENS = 20


class ChatClientError(Exception):
    """The request failed or the response carried no completion."""


class ChatClientInterface(Protocol):
    """Sends one system+user message pair and returns the model's reply text.

    This is the seam the move strategy depends on: any provider — whatever its
    wire shape — plugs in by implementing this one method.
    """

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        ...


class TransportInterface(Protocol):
    """POSTs a JSON payload and returns the decoded JSON response."""

    def post_json(
        self, url: str, headers: Dict[str, str], payload: dict, timeout_s: float
    ) -> dict:
        ...


class UrllibTransport:
    """Stdlib transport — one small POST does not warrant a dependency."""

    def post_json(
        self, url: str, headers: Dict[str, str], payload: dict, timeout_s: float
    ) -> dict:
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ChatClientError(f"LLM request failed: {exc}") from exc


class OpenAiCompatChatClient:
    """ChatClientInterface over any OpenAI-compatible chat-completions endpoint.

    The endpoint URL, API key, and model all arrive from the provider registry;
    nothing in this class names a specific vendor.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        model: str,
        transport: Optional[TransportInterface] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not url:
            raise ValueError("url must be provided")
        if not api_key:
            raise ValueError("api_key must be provided")
        if not model:
            raise ValueError("model must be provided")
        self._url = url
        self._api_key = api_key
        self._model = model
        self._transport = transport or UrllibTransport()
        self._timeout_s = timeout_s

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """One chat completion; raises ChatClientError on transport or shape failure."""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # Temperature 0: the strategy wants the model's single best pick,
            # not creative variety — variety comes from the game itself.
            "temperature": 0,
            "max_tokens": MAX_COMPLETION_TOKENS,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        response = self._transport.post_json(self._url, headers, payload, self._timeout_s)
        return self._extract_reply(response)

    def _extract_reply(self, response: dict) -> str:
        try:
            return response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatClientError(f"LLM response had no completion: {response!r}") from exc
