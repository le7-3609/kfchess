"""Unit tests for the pure domain PlayerSession's fail-fast invariants.

Transport-level behavior (socket liveness, JSON send) is covered by
tests/unit/test_session.py against the infrastructure wrapper that composes
this entity.
"""

import pytest

from server.domain.session.player_session import ConnectionState, PlayerSession


def test_rejects_an_empty_username():
    with pytest.raises(ValueError):
        PlayerSession(username="", user_id=1)


def test_rejects_a_blank_username():
    with pytest.raises(ValueError):
        PlayerSession(username="   ", user_id=1)


def test_defaults_to_connected_with_no_assigned_color():
    session = PlayerSession(username="Alice", user_id=1)

    assert session.connection_state == ConnectionState.CONNECTED
    assert session.is_connected
    assert session.color is None
    assert session.elo == 1200


def test_assign_color_rejects_an_unknown_color():
    session = PlayerSession(username="Alice", user_id=1)

    with pytest.raises(ValueError):
        session.assign_color("purple")


def test_assign_color_accepts_white_and_black():
    session = PlayerSession(username="Alice", user_id=1)

    session.assign_color("w")
    assert session.color == "w"

    session.assign_color("b")
    assert session.color == "b"


def test_disconnect_then_reconnect_round_trips_state():
    session = PlayerSession(username="Alice", user_id=1)

    session.disconnect()
    assert session.connection_state == ConnectionState.DISCONNECTED
    assert not session.is_connected

    session.reconnect()
    assert session.connection_state == ConnectionState.CONNECTED
    assert session.is_connected


def test_reconnect_rejects_an_already_connected_session():
    session = PlayerSession(username="Alice", user_id=1)

    with pytest.raises(ValueError):
        session.reconnect()
