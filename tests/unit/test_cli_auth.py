"""Unit tests for the CLI auth handshake's credential/ELO capture.

Exercises `prompt_authentication` against a fake websocket connection, with
`_collect_credentials` patched out so the tests never touch stdin.
"""

import json
import unittest
from typing import Any, Dict
from unittest.mock import patch

from client.auth import cli_auth


class _FakeConnection:
    """Minimal async context manager standing in for a `websockets` connection."""

    def __init__(self, reply: Dict[str, Any]) -> None:
        self.sent_payloads = []
        self._reply = reply

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def send(self, payload: str) -> None:
        self.sent_payloads.append(payload)

    async def recv(self) -> str:
        return json.dumps(self._reply)


class TestPromptAuthentication(unittest.IsolatedAsyncioTestCase):
    async def test_successful_login_captures_elo_from_the_auth_reply(self):
        credentials = cli_auth.UserCredentials(action="login", username="alice", password="secret")
        connection = _FakeConnection({"type": "auth", "status": "ok", "username": "alice", "elo": 1350})

        with patch.object(cli_auth, "_collect_credentials", return_value=credentials), \
                patch.object(cli_auth.websockets, "connect", side_effect=lambda url: connection):
            result = await cli_auth.prompt_authentication("ws://fake")

        self.assertEqual(result.username, "alice")
        self.assertEqual(result.password, "secret")
        self.assertEqual(result.elo, 1350)

    async def test_missing_elo_in_reply_defaults_to_zero(self):
        credentials = cli_auth.UserCredentials(action="register", username="bob", password="secret")
        connection = _FakeConnection({"type": "auth", "status": "ok", "username": "bob"})

        with patch.object(cli_auth, "_collect_credentials", return_value=credentials), \
                patch.object(cli_auth.websockets, "connect", side_effect=lambda url: connection):
            result = await cli_auth.prompt_authentication("ws://fake")

        self.assertEqual(result.elo, 0)

    async def test_failed_auth_terminates_the_process(self):
        credentials = cli_auth.UserCredentials(action="login", username="alice", password="wrong")
        connection = _FakeConnection({"type": "error", "message": "invalid credentials"})

        with patch.object(cli_auth, "_collect_credentials", return_value=credentials), \
                patch.object(cli_auth.websockets, "connect", side_effect=lambda url: connection):
            with self.assertRaises(SystemExit):
                await cli_auth.prompt_authentication("ws://fake")


if __name__ == "__main__":
    unittest.main()
