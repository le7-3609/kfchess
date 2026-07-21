"""IGameController — the seam a game window plays a match through (Layer 6 / client).

Owns: the vocabulary the UI and a match driver exchange — moves and jumps
going out, typed session/snapshot/event/notice callbacks coming back — plus
the capability flags that let a single window serve a mode with no opponent to
disconnect and a mode with no local clock to advance.
Must not own: sockets, wire frames, tkinter widgets, or game rules. The two
implementations live in client/controllers/network_game_controller.py and
client/controllers/local_game_controller.py; this module names only what they share, so
GameWindow can hold either without ever learning which one it got.

Every callback is delivered on the UI thread, from inside `poll()`. A
controller that receives work on some other thread — NetworkGameController's
socket thread — must buffer it and replay it there, because the listener on
the other end touches tkinter and tkinter tolerates no other thread.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Protocol

from shared.io.game_history_store import SavedGame
from shared.io.moves_log import MoveLogEntry
from shared.model.position import Position
from shared.view.game_snapshot import GameSnapshot


@dataclass(frozen=True)
class GameSessionInfo:
    """Who this window is playing as, and against whom.

    *assigned_color* is a `consts.COLOR_*` code, or None for a spectator —
    which `is_viewer` states outright rather than leaving the window to infer
    it from a missing color.
    """

    assigned_color: Optional[str]
    opponent_name: str
    room_id: Optional[str] = None
    is_viewer: bool = False


class NoticeLevel(Enum):
    """How long a notice is expected to stand.

    TRANSIENT covers a situation still being recovered from (a reconnect
    attempt, a disconnect countdown) and can be superseded or cleared.
    TERMINAL means the match is over and the only action left is closing.
    CLEARED withdraws whatever notice was showing.
    """

    TRANSIENT = "transient"
    TERMINAL = "terminal"
    CLEARED = "cleared"


@dataclass(frozen=True)
class GameNotice:
    """A message for the player, already phrased for display.

    The controller composes the wording rather than handing the window a
    reason code, because what there is to say is mode-specific: only a
    networked match can report a rating change or an opponent who dropped, and
    only it knows which seat this window occupies. The window's job is to show
    the string at the right level, not to interpret it.

    *outcome* is this seat's personal result — True for a win, False for a
    loss, None when there isn't one (a draw, a spectator, hotseat play with no
    assigned seat, or any non-terminal notice). It is stated here, rather than
    parsed from *text*, because only the controller knows both the winner and
    this seat's color.
    """

    level: NoticeLevel
    text: str = ""
    outcome: Optional[bool] = None

    @staticmethod
    def cleared() -> "GameNotice":
        return GameNotice(level=NoticeLevel.CLEARED)


class GameControllerListener(ABC):
    """What a controller reports back; implemented by the game window.

    Called only from `IGameController.poll()`, on the UI thread.
    """

    @abstractmethod
    def on_session_started(self, session: GameSessionInfo) -> None:
        """The seat assignment is known and play is about to begin."""

    @abstractmethod
    def on_snapshot(self, snapshot: GameSnapshot) -> None:
        """A fresh render DTO for the current instant."""

    @abstractmethod
    def on_move_recorded(self, entry: MoveLogEntry) -> None:
        """A move resolved and belongs in the move list."""

    @abstractmethod
    def on_score_changed(self, white_score: int, black_score: int) -> None:
        """Material totals changed."""

    @abstractmethod
    def on_capture(self, pos: Position, at_ms: int) -> None:
        """A piece was captured on *pos* at simulation time *at_ms*."""

    @abstractmethod
    def on_notice(self, notice: GameNotice) -> None:
        """Something the player must be told, or the withdrawal of one."""


class MatchHistoryPort(Protocol):
    """The slice of history persistence the save/load dialogs actually use.

    Narrowed to three methods so the dialogs depend on what they call rather
    than on the whole GameService — which the networked mode has no local
    instance of at all. GameService satisfies this structurally.
    """

    def save_history(
        self, save_name: str, white_name: str, black_name: str, winner: Optional[str]
    ) -> str: ...

    def list_saves(self) -> List[str]: ...

    def load_saved_game(self, file_name: str) -> SavedGame: ...


class IGameController(ABC):
    """Drives one match on behalf of the window, over a network or in memory."""

    @property
    @abstractmethod
    def poll_interval_ms(self) -> int:
        """How often the window should call `poll()`.

        A local controller advances the simulation on this beat and wants the
        frame budget; a networked one only drains already-computed frames and
        can afford to look less often.
        """

    @property
    def assigned_color(self) -> Optional[str]:
        """This window's seat, or None before it is known / for a spectator."""
        return None

    @property
    def is_viewer(self) -> bool:
        """True when this window may watch but not move."""
        return False

    @property
    def supports_jump(self) -> bool:
        """Whether `submit_jump` does anything.

        The wire protocol has no jump frame, so a networked window must not
        offer the gesture at all rather than bind a control that silently
        does nothing.
        """
        return False

    @property
    def supports_preferences(self) -> bool:
        """Whether `apply_preferences` reaches a simulation this window owns.

        Movement speed and cooldown are properties of the running game, so
        only a locally-simulated match can honour a change to them; a
        networked seat must not appear to change what the server decides.
        """
        return False

    @property
    def history(self) -> Optional[MatchHistoryPort]:
        """History persistence for this match, or None if it keeps none."""
        return None

    @abstractmethod
    def start(self, listener: GameControllerListener) -> None:
        """Begin the match and direct every callback at *listener*."""

    @abstractmethod
    def poll(self) -> None:
        """Advance the match and deliver whatever it produced, on the UI thread."""

    @abstractmethod
    def submit_move(self, source: Position, target: Position) -> None:
        """Ask for the piece on *source* to move to *target*."""

    def submit_select(self, pos: Position) -> None:
        """Announce that the player picked *pos* up as a move's source.

        The window highlights its own pending source either way, so a mode
        with no engine of its own leaves this a no-op. A locally-simulated
        match forwards it, because only the engine can work out which squares
        that piece may legally reach — and the answer comes back in the next
        snapshot rather than through a return value.
        """

    @abstractmethod
    def leave(self) -> None:
        """Tear the match down; safe to call more than once."""

    def submit_jump(self, pos: Position) -> None:
        """Jump the piece on *pos* in place, if this mode supports it.

        Default no-op, guarded by `supports_jump` — see that property.
        """

    def apply_preferences(self, ms_per_square: int, cooldown_ms: int) -> None:
        """Apply movement speed / cooldown, if this mode supports it.

        Default no-op, guarded by `supports_preferences` — see that property.
        """
