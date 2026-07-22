"""SoundPlayer: cue lookup, header validation, and platform playback,
isolated from any real audio.

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
    stub.SND_NODEFAULT = 16
    monkeypatch.setattr(sound_player_module, "winsound", stub)
    return stub


@pytest.fixture
def assets_dir(tmp_path):
    """An assets folder whose sound cues carry a valid RIFF header."""
    sounds = tmp_path / ui_consts.SOUNDS_DIR_NAME
    sounds.mkdir()
    for file_name in (ui_consts.SOUND_FILE_WIN, ui_consts.SOUND_FILE_LOSE):
        (sounds / file_name).write_bytes(b"RIFF fake wave data")
    return str(tmp_path)


def test_play_win_and_play_lose_use_their_own_files(fake_winsound, assets_dir):
    player = SoundPlayer(assets_dir)

    player.play_win()
    player.play_lose()

    played = [call.args[0] for call in fake_winsound.PlaySound.call_args_list]
    assert played[0].endswith(ui_consts.SOUND_FILE_WIN)
    assert played[1].endswith(ui_consts.SOUND_FILE_LOSE)


def test_cues_never_use_the_default_chime_fallback(fake_winsound, assets_dir):
    player = SoundPlayer(assets_dir)

    player.play_win()

    _, flags = fake_winsound.PlaySound.call_args.args
    assert flags & fake_winsound.SND_NODEFAULT


def test_a_missing_cue_file_is_skipped_silently(fake_winsound, assets_dir):
    missing = os.path.join(assets_dir, ui_consts.SOUNDS_DIR_NAME, ui_consts.SOUND_FILE_WIN)
    os.remove(missing)
    player = SoundPlayer(assets_dir)

    player.play_win()

    fake_winsound.PlaySound.assert_not_called()


def test_a_cue_without_a_riff_header_is_skipped_silently(fake_winsound, assets_dir):
    """An MP3 renamed to .wav must not reach winsound, which would fall back
    to the Windows default error chime."""
    disguised_mp3 = os.path.join(
        assets_dir, ui_consts.SOUNDS_DIR_NAME, ui_consts.SOUND_FILE_WIN
    )
    with open(disguised_mp3, "wb") as f:
        f.write(b"ID3\x04 not really a wave file")
    player = SoundPlayer(assets_dir)

    player.play_win()

    fake_winsound.PlaySound.assert_not_called()


def test_no_assets_dir_never_touches_the_platform_player(fake_winsound):
    player = SoundPlayer(None)

    player.play_win()

    fake_winsound.PlaySound.assert_not_called()


def test_unavailable_platform_player_is_a_silent_no_op(monkeypatch, tmp_path):
    monkeypatch.setattr(sound_player_module, "winsound", None)
    player = SoundPlayer(str(tmp_path))

    player.play_win()  # must not raise


def test_a_platform_playback_failure_is_swallowed(fake_winsound, assets_dir):
    fake_winsound.PlaySound.side_effect = OSError("no audio device")
    player = SoundPlayer(assets_dir)

    player.play_win()  # must not raise
