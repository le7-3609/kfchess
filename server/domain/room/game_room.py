"""Game room aggregate — room lifecycle, seat bindings, and move dispatch.

Layer: domain (server/domain/room)
Owns: room lifecycle state (WAITING / PLAYING / FINISHED), White/Black/viewer
seat bindings, seat-assignment invariants, move authorization, and delegating
live simulation to shared.service.GameService.
Must not own: WebSocket transport, event broadcasting, database persistence,
bot driving, or disconnect/reconnect timers — those live in
server.application.game_room.GameRoom, the orchestrating room that composes
this aggregate.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Optional

from shared.bootstrap import build_core
from shared.config import consts
from shared.config.game_config import GameConfig
from shared.events import PieceMovedEvent
from shared.io.game_history_store import GameHistoryStore
from shared.io.moves_log import MovesLog
from shared.model.game_state import Result
from shared.model.position import Position
from shared.realtime.real_time_arbiter import ChebyshevDistanceDuration
from shared.service import GameService
from server.domain.matchmaking.elo import calculate_elo
from server.domain.room.room_role import RoomRole

_LOGGER = logging.getLogger(__name__)


class RoomState(Enum):
    WAITING = "waiting"
    PLAYING = "playing"
    FINISHED = "finished"


@dataclass(frozen=True)
class ForfeitOutcome:
    """New ratings resulting from a forfeit, for the caller to persist."""

    winner_session: Any
    loser_session: Any
    new_winner_elo: int
    new_loser_elo: int


@dataclass(frozen=True)
class GameEndOutcome:
    """New ratings resulting from a game that ended on its own merits, for the caller to persist.

    Framed by seat (white/black) rather than winner/loser, since a draw has no
    winner but still rates both players.
    """

    white_session: Any
    black_session: Any
    new_white_elo: int
    new_black_elo: int


class GameRoom:
    """Aggregate root for a single game: seats, lifecycle state, and move dispatch."""

    def __init__(self, room_id: str) -> None:
        if not room_id or not room_id.strip():
            raise ValueError("room_id must not be empty")

        self._room_id = room_id
        self._state = RoomState.WAITING

        self._white_player: Optional[Any] = None
        self._black_player: Optional[Any] = None
        self._viewers: List[Any] = []

        self._config: Optional[GameConfig] = None
        self._core: Optional[Any] = None
        self._service: Optional[GameService] = None
        self._engine: Optional[Any] = None

    @property
    def room_id(self) -> str:
        return self._room_id

    @property
    def state(self) -> RoomState:
        return self._state

    @property
    def is_full(self) -> bool:
        return self._white_player is not None and self._black_player is not None

    @property
    def white_player(self) -> Optional[Any]:
        return self._white_player

    @property
    def black_player(self) -> Optional[Any]:
        return self._black_player

    @property
    def viewers(self) -> List[Any]:
        return list(self._viewers)

    @property
    def viewer_count(self) -> int:
        return len(self._viewers)

    @property
    def config(self) -> Optional[GameConfig]:
        return self._config

    @property
    def core(self) -> Optional[Any]:
        return self._core

    @property
    def service(self) -> Optional[GameService]:
        return self._service

    @property
    def engine(self) -> Optional[Any]:
        return self._engine

    def role_of(self, session: Any) -> Optional[RoomRole]:
        """Return the seat *session* holds here, or None if it is not a participant."""
        if session is self._white_player:
            return RoomRole.WHITE_PLAYER
        if session is self._black_player:
            return RoomRole.BLACK_PLAYER
        if session in self._viewers:
            return RoomRole.VIEWER
        return None

    def opponent_of(self, session: Any) -> Optional[Any]:
        """Return the other seated player, or None if *session* holds no seat."""
        if session is self._white_player:
            return self._black_player
        if session is self._black_player:
            return self._white_player
        return None

    def find_player_by_username(self, username: str) -> Optional[Any]:
        """Look up a seated (White or Black) session by username, ignoring viewers."""
        for session in (self._white_player, self._black_player):
            if session is not None and session.username == username:
                return session
        return None

    def add_player(self, session: Any) -> RoomRole:
        """Assign player session to White or Black slot.

        Raises:
            ValueError: If both player slots are occupied.
        """
        if self._white_player is None:
            self._white_player = session
            session.assign_color(consts.COLOR_WHITE)
            return RoomRole.WHITE_PLAYER
        elif self._black_player is None:
            self._black_player = session
            session.assign_color(consts.COLOR_BLACK)
            self._initialize_game()
            return RoomRole.BLACK_PLAYER
        else:
            raise ValueError("Room player slots are full")

    def add_viewer(self, session: Any) -> None:
        """Add a spectator to the room."""
        self._viewers.append(session)

    def remove_participant(self, session: Any) -> None:
        if self._white_player is session:
            self._white_player = None
        elif self._black_player is session:
            self._black_player = None
        elif session in self._viewers:
            self._viewers.remove(session)

    def mark_finished(self) -> None:
        self._state = RoomState.FINISHED

    def compute_forfeit_outcome(
        self, disconnected_session: Any, opponent_session: Optional[Any]
    ) -> Optional[ForfeitOutcome]:
        """Compute the ELO adjustment for a forfeit, without persisting or mutating state.

        A bot holds no account row, so rating a game against one would both
        no-op in the database and corrupt the human's stored rating.
        """
        if opponent_session is None:
            return None

        involves_bot = getattr(disconnected_session, "is_bot", False) or getattr(
            opponent_session, "is_bot", False
        )
        if involves_bot:
            return None

        new_winner_elo, new_loser_elo = calculate_elo(
            winner_elo=opponent_session.elo, loser_elo=disconnected_session.elo
        )
        return ForfeitOutcome(
            winner_session=opponent_session,
            loser_session=disconnected_session,
            new_winner_elo=new_winner_elo,
            new_loser_elo=new_loser_elo,
        )

    def compute_game_end_outcome(self, winner_color: Optional[str]) -> Optional[GameEndOutcome]:
        """Compute the ELO adjustment for a game that ended on its own merits.

        *winner_color* is consts.COLOR_WHITE/COLOR_BLACK for a decisive result
        (checkmate, king capture) or None for a draw (stalemate, insufficient
        material, repetition, fifty-move) — the same shape GameEndedEvent.winner
        carries. Does not persist or mutate any state.

        A bot holds no account row, so rating a game against one would both
        no-op in the database and corrupt the human's stored rating.
        """
        white = self._white_player
        black = self._black_player
        if white is None or black is None:
            return None

        if getattr(white, "is_bot", False) or getattr(black, "is_bot", False):
            return None

        if winner_color is None:
            new_white_elo, new_black_elo = calculate_elo(
                winner_elo=white.elo, loser_elo=black.elo, draw=True
            )
        elif winner_color == consts.COLOR_WHITE:
            new_white_elo, new_black_elo = calculate_elo(winner_elo=white.elo, loser_elo=black.elo)
        elif winner_color == consts.COLOR_BLACK:
            new_black_elo, new_white_elo = calculate_elo(winner_elo=black.elo, loser_elo=white.elo)
        else:
            _LOGGER.warning(
                "Room %s: game-end outcome requested with unknown winner color %r",
                self._room_id, winner_color,
            )
            return None

        return GameEndOutcome(
            white_session=white,
            black_session=black,
            new_white_elo=new_white_elo,
            new_black_elo=new_black_elo,
        )

    def handle_move(
        self, session: Any, from_sq: str, src_pos: Position, dst_pos: Position
    ) -> Result[None, str]:
        """Authorize and dispatch an already-parsed move directly against the GameEngine.

        *from_sq* is carried through only for authorization error messages —
        parsing algebraic squares into Position is a wire-format concern owned
        by the infrastructure room that calls this.
        """
        authorization = self._authorize_move(session)
        if not authorization.is_ok:
            return authorization

        ownership = self._authorize_piece_ownership(session, src_pos, from_sq)
        if not ownership.is_ok:
            return ownership

        self._engine.request_move(src_pos, dst_pos)
        return Result.ok(None)

    def _authorize_move(self, session: Any) -> Result[None, str]:
        """Gate a move on the room being live and *session* holding a player seat."""
        if self._state != RoomState.PLAYING or self._engine is None:
            _LOGGER.warning("Room %s cannot handle move: game not active", self._room_id)
            return Result.fail("Game is not currently active")

        if session is not self._white_player and session is not self._black_player:
            _LOGGER.warning(
                "Non-player session %s attempted to move in room %s",
                getattr(session, "username", "?"), self._room_id,
            )
            return Result.fail("Spectators cannot submit moves")

        return Result.ok(None)

    def _authorize_piece_ownership(
        self, session: Any, src_pos: Position, from_sq: str
    ) -> Result[None, str]:
        """Enforce that *session* only moves pieces of its own assigned color."""
        board = self._engine._board_repo.get_board()
        if board is None:
            return Result.ok(None)

        piece = board.get_piece(src_pos)
        if piece is None or piece.color != session.color:
            _LOGGER.warning(
                "Player %s (%s) attempted to move piece at %s (%s)",
                session.username, session.color, from_sq, piece.color if piece else "empty"
            )
            return Result.fail(f"No piece of your color at {from_sq}")

        return Result.ok(None)

    def _initialize_game(self) -> None:
        """Set up the core game engine and service. Called once both seats are filled."""
        self._config = GameConfig()
        core = build_core(
            self._config,
            require_kings=True,
            duration_strategy=ChebyshevDistanceDuration(ms_per_square=self._config.ms_per_square),
        )
        # Retained so a bot seated here can be built against these exact
        # repositories — a bot wired to a different core would read a board
        # that never changes.
        self._core = core

        moves_log = MovesLog()
        core.event_bus.subscribe(moves_log, PieceMovedEvent)

        self._service = GameService(
            board_repo=core.board_repo,
            state_repo=core.state_repo,
            parser=core.parser,
            validator=core.validator,
            engine=core.engine,
            config=self._config,
            arbiter=core.arbiter,
            moves_log=moves_log,
            history_store=GameHistoryStore(),
            event_bus=core.event_bus,
        )
        self._engine = core.engine

        init_res = self._service.init_game(consts.STARTING_POSITION.splitlines())
        if not init_res.is_ok:
            _LOGGER.error("Failed to initialize room %s game position: %s", self._room_id, init_res.error)

        self._state = RoomState.PLAYING
