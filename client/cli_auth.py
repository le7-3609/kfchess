"""Interactive CLI authentication for the multiplayer client (client layer, pre-GUI).

Owns: the terminal login/register menu, credential capture, and the short-lived
WebSocket auth handshake performed before the Tk window is created.
Must not own: GUI components, game logic, or the persistent game connection.
"""

import asyncio
import getpass
import json
import logging
import sys
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, NoReturn

import websockets

_LOGGER = logging.getLogger(__name__)

AUTH_TIMEOUT_SECONDS = 10.0

# Wire protocol vocabulary (mirrors server/application/dtos; client must not import server).
_MSG_TYPE_AUTH = "auth"
_MSG_TYPE_ERROR = "error"
_ACTION_LOGIN = "login"
_ACTION_REGISTER = "register"

_MENU = """
==================================
   Kung Fu Chess — Multiplayer
==================================
  [1] Login
  [2] Register
"""

_MENU_ACTIONS: Dict[str, str] = {"1": _ACTION_LOGIN, "2": _ACTION_REGISTER}


@dataclass(frozen=True)
class UserCredentials:
    """Validated authentication input captured from the terminal.

    `elo` starts at 0 and is filled in by `prompt_authentication` once the
    server's auth-success reply reports the player's actual rating — the
    Lobby's header and its ELO-bounded matchmaking search both need it.
    """

    action: str
    username: str
    password: str
    elo: int = 0


def _fail(message: str) -> NoReturn:
    """Log a failure, inform the user, and terminate the process."""
    _LOGGER.error(message)
    print(f"\n{message}", file=sys.stderr)
    sys.exit(1)


def _prompt_action() -> str:
    """Show the menu and return the chosen auth action, re-prompting until valid."""
    print(_MENU)
    while True:
        choice = input("Select an option [1-2]: ").strip()
        action = _MENU_ACTIONS.get(choice)
        if action is not None:
            return action
        print("Invalid selection — please enter 1 or 2.")


def _prompt_non_empty(label: str, reader: Callable[[str], str]) -> str:
    """Read a value with the given reader, re-prompting until it is non-empty."""
    while True:
        value = reader(label)
        if value:
            return value
        print("Input cannot be empty.")


def _collect_credentials() -> UserCredentials:
    """Run the full interactive prompt sequence and return validated credentials."""
    action = _prompt_action()
    username = _prompt_non_empty("Username: ", lambda label: input(label).strip())
    password = _prompt_non_empty("Password: ", getpass.getpass)
    return UserCredentials(action=action, username=username, password=password)


def _build_auth_frame(credentials: UserCredentials) -> str:
    """Serialize credentials into the JSON auth frame the server expects."""
    return json.dumps(
        {
            "type": _MSG_TYPE_AUTH,
            "action": credentials.action,
            "username": credentials.username,
            "password": credentials.password,
        }
    )


def _decode_response(raw_frame: str | bytes) -> Dict[str, Any]:
    """Parse a raw server frame into a message dict.

    Raises:
        ValueError: If the frame is not a JSON object with a 'type' field.
    """
    try:
        message = json.loads(raw_frame)
    except json.JSONDecodeError as exc:
        raise ValueError(f"server sent invalid JSON: {exc}") from exc

    if not isinstance(message, dict) or "type" not in message:
        raise ValueError("server frame is missing the 'type' field")
    return message


async def _perform_handshake(server_url: str, credentials: UserCredentials) -> Dict[str, Any]:
    """Open a short-lived connection, send the auth frame, and return the reply."""
    async with asyncio.timeout(AUTH_TIMEOUT_SECONDS):
        async with websockets.connect(server_url) as connection:
            await connection.send(_build_auth_frame(credentials))
            raw_reply = await connection.recv()
    return _decode_response(raw_reply)


async def prompt_authentication(server_url: str) -> UserCredentials:
    """Authenticate interactively against the server and return the credentials used.

    Prompts for login/register plus credentials on stdin, performs the WebSocket
    handshake against ``server_url``, and exits the process on any failure so the
    GUI never starts with an unauthenticated user. The returned `UserCredentials`
    (including the now-verified password and the server-reported ELO) lets the
    caller re-authenticate the persistent game connection later without
    prompting the user twice — this handshake's socket is short-lived and
    closed once it returns.
    """
    try:
        credentials = await asyncio.to_thread(_collect_credentials)
    except (EOFError, KeyboardInterrupt):
        _fail("Authentication cancelled.")

    try:
        response = await _perform_handshake(server_url, credentials)
    except TimeoutError:
        _fail(f"Server did not respond within {AUTH_TIMEOUT_SECONDS:.0f}s: {server_url}")
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        _fail(f"Could not reach the server at {server_url}: {exc}")
    except ValueError as exc:
        _fail(f"Malformed server response: {exc}")

    if response.get("type") == _MSG_TYPE_ERROR:
        _fail(f"Authentication failed: {response.get('message', 'unknown error')}")

    credentials = replace(credentials, elo=response.get("elo", 0))
    print(f"\nWelcome, {credentials.username}! Authentication successful. (ELO: {credentials.elo})")
    return credentials
