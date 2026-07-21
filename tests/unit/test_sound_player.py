"""SoundPlayer: cue lookup and platform playback, isolated from any real audio.

`winsound` is monkeypatched to a stub so these run identically on every
platform regardless of whether the real module is importable here.
"""

import os
from unittest.mock import MagicMock

import pytest

from client.ui import consts as ui_consts
from client.ui import sound_player as sound_player_module
from client.ui.sound_player import SoundPlayer


@pytest.fixture
def fake_winsound(monkeypatch):
    stub = MagicMock()
    stub.SND_FILENAME = 1
    stub.SND_ASYNC = 2
    monkeypatch.setattr(sound_player_module, "winsound", stub)
    return stub


def test_play_move_plays_the_move_file_from_the_sounds_subfolder(fake_winsound):
    player = SoundPlayer("assets")

    player.play_move()

    expected_path = os.path.join("assets", ui_consts.SOUNDS_DIR_NAME, ui_consts.SOUND_FILE_MOVE)
    fake_winsound.PlaySound.assert_called_once_with(
        expected_path, fake_winsound.SND_FILENAME | fake_winsound.SND_ASYNC
    )


def test_play_win_and_play_lose_use_their_own_files(fake_winsound):
    player = SoundPlayer("assets")

    player.play_win()
    player.play_lose()

    played = [call.args[0] for call in fake_winsound.PlaySound.call_args_list]
    assert played[0].endswith(ui_consts.SOUND_FILE_WIN)
    assert played[1].endswith(ui_consts.SOUND_FILE_LOSE)


def test_no_assets_dir_never_touches_the_platform_player(fake_winsound):
    player = SoundPlayer(None)

    player.play_move()

    fake_winsound.PlaySound.assert_not_called()


def test_unavailable_platform_player_is_a_silent_no_op(monkeypatch):
    monkeypatch.setattr(sound_player_module, "winsound", None)
    player = SoundPlayer("assets")

    player.play_move()  # must not raise


def test_a_platform_playback_failure_is_swallowed(fake_winsound):
    fake_winsound.PlaySound.side_effect = OSError("no audio device")
    player = SoundPlayer("assets")

    player.play_move()  # must not raise
