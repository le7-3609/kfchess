"""Unit tests for PlayerSession connectivity detection."""

import pytest

from server.domain.session.player_session import ConnectionState
from server.presentation.ws_connection import PlayerSession, is_socket_open


class ModernSocket:
    """websockets >= 14 exposes close_code but no `.open` attribute."""

    def __init__(self, close_code=None):
        self.close_code = close_code


class LegacySocket:
    """Older releases (and existing test doubles) expose `.open`."""

    def __init__(self, open: bool):
        self.open = open


def test_open_socket_without_legacy_attribute_is_connected():
    assert is_socket_open(ModernSocket()) is True


def test_closing_socket_is_detected_via_close_code():
    """Without this, `connected` is permanently True on modern websockets and
    the server keeps broadcasting into dead connections.
    """
    assert is_socket_open(ModernSocket(close_code=1000)) is False


def test_legacy_open_attribute_is_still_honoured():
    assert is_socket_open(LegacySocket(open=True)) is True
    assert is_socket_open(LegacySocket(open=False)) is False


def test_missing_socket_is_never_open():
    assert is_socket_open(None) is False


def test_session_reports_closed_socket_as_disconnected():
    session = PlayerSession(websocket=ModernSocket(), username="Alice", user_id=1)
    assert session.connected

    session.websocket = ModernSocket(close_code=1006)
    assert not session.connected


def test_explicit_disconnect_overrides_a_live_socket():
    session = PlayerSession(websocket=ModernSocket(), username="Alice", user_id=1)
    session.disconnect()

    assert session.connection_state == ConnectionState.DISCONNECTED
    assert not session.connected
