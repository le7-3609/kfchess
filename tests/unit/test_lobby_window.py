"""Unit tests for LobbyWindow's handoff of an already-running NetworkClient
to the game window it launches.

Regression coverage for a bug where the lobby started the persistent
connection with its own message queue, then the game window took over
without ever being wired to receive further frames (or polling its own
queue) — so a `game_state` frame landing after `game_start` was silently
lost and the board never rendered. Constructs LobbyWindow via __new__ so no
real Tk root is ever created.
"""

import queue
from unittest.mock import MagicMock

from client.ui.window.lobby_window import LobbyWindow


def _bare_lobby() -> LobbyWindow:
    lobby = LobbyWindow.__new__(LobbyWindow)
    lobby._message_queue = queue.Queue()
    return lobby


def test_forward_pending_frames_replays_queued_messages_onto_the_game_window():
    lobby = _bare_lobby()
    score_msg = {"type": "event_score_updated", "white_score": 1, "black_score": 0}
    game_state_msg = {"type": "game_state", "state": {}}
    lobby._message_queue.put(score_msg)
    lobby._message_queue.put(game_state_msg)

    controller = MagicMock()
    lobby._forward_pending_frames(controller)

    assert [c.args[0] for c in controller.accept_frame.call_args_list] == [
        score_msg,
        game_state_msg,
    ]
    assert lobby._message_queue.empty()


def test_forward_pending_frames_is_a_noop_when_nothing_is_queued():
    lobby = _bare_lobby()
    controller = MagicMock()

    lobby._forward_pending_frames(controller)

    controller.accept_frame.assert_not_called()

