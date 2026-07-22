"""Collision resolution between concurrently moving pieces (Layer 4).

Detects same-square and crossing (swap-path) collisions among movements
active at a given tick, and applies KungFu Chess's winner/loser rules:
enemy collisions capture, friendly collisions abort the later mover into
cooldown. Castling's simultaneous King+Rook arrival is exempted.
"""

from typing import Callable, List, Optional, Tuple

from shared.config import consts
from shared.model.position import Position
from shared.model.board import BoardInterface
from shared.model.game_state import GameState, Movement, Cooldown
from shared.events import (
    ABORT_REASON_FRIENDLY_COLLISION,
    EventBus,
    GameEndedEvent,
    MoveAbortedEvent,
    PieceCapturedEvent,
)


class CollisionResolver:
    """Resolves same-square/crossing collisions for a single tick."""

    def __init__(
        self,
        config: 'GameConfig',  # type: ignore[name-defined]
        position_at: Callable[[Movement, int], Position],
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._config = config
        self._position_at = position_at
        # An unsubscribed bus rather than None, so publish sites stay unguarded.
        self._event_bus = event_bus or EventBus()

    def resolve(
        self,
        board: BoardInterface,
        state: GameState,
        movements: List[Movement],
        current_active: List[Movement],
        positions: dict,
        t: int,
        t_prev: Optional[int],
    ) -> bool:
        """Detect and resolve collisions among the movements active at *t*.

        Takes the tick's active movements plus their precomputed positions
        (keyed by ``id(movement)``), compares every pair, and mutates *board*,
        *state*, and *movements* to apply the outcome of each collision found.
        Losing movements are dropped from *movements*.

        Returns whether the halfmove clock should reset.
        """
        reset_halfmove = False
        losers: set = set()

        for mov1, mov2 in self._unresolved_pairs(current_active, losers):
            if not self._collides(mov1, mov2, positions, t_prev):
                continue
            if self._is_simultaneous_castling_pair(mov1, mov2):
                continue

            winner, loser = self._pick_winner_and_loser(mov1, mov2, movements)
            losers.add(id(loser))

            if winner.piece.color != loser.piece.color:
                self._apply_capture(board, state, loser, winner, positions[id(loser)], t)
                reset_halfmove = True
            else:
                self._abort_into_cooldown(board, state, loser, positions[id(loser)], t)

        movements[:] = [mov for mov in movements if id(mov) not in losers]
        return reset_halfmove

    def _unresolved_pairs(self, current_active: List[Movement], losers: set):
        """Yield each pair of active movements that has not already lost a collision.

        A movement that lost an earlier pairing is already captured or aborted,
        so pairing it again would apply a second outcome to a dead piece.
        """
        for i, mov1 in enumerate(current_active):
            for mov2 in current_active[i + 1:]:
                if id(mov1) in losers or id(mov2) in losers:
                    continue
                yield mov1, mov2

    def _collides(self, mov1: Movement, mov2: Movement, positions: dict, t_prev: Optional[int]) -> bool:
        """True if the two movements share a square at this tick or crossed paths since *t_prev*."""
        pos1 = positions[id(mov1)]
        pos2 = positions[id(mov2)]
        if pos1 == pos2:
            return True
        return self._crossed_paths(mov1, mov2, pos1, pos2, t_prev)

    def _crossed_paths(
        self, mov1: Movement, mov2: Movement, pos1: Position, pos2: Position, t_prev: Optional[int]
    ) -> bool:
        """True if the two movements swapped squares between *t_prev* and now.

        Pieces travel continuously but are only sampled once per tick, so two
        pieces moving through each other never register as sharing a square —
        the swap has to be detected against the previous tick's positions.
        """
        if t_prev is None:
            return False
        pos1_prev = self._position_at(mov1, t_prev)
        pos2_prev = self._position_at(mov2, t_prev)
        return pos1 == pos2_prev and pos2 == pos1_prev

    def _is_simultaneous_castling_pair(self, mov1: Movement, mov2: Movement) -> bool:
        """True if the two movements are the King and Rook legs of one castle.

        A castle's legs necessarily cross and briefly share squares mid-transit;
        without this exemption the normal rules would abort both. Mirrors the
        castling exemption in ArrivalResolver.
        """
        return (
            mov1.piece.color == mov2.piece.color
            and mov1.arrival_ms == mov2.arrival_ms
            and {mov1.piece.piece_type, mov2.piece.piece_type} == consts.CASTLING_PIECE_PAIR
        )

    def _pick_winner_and_loser(
        self, mov1: Movement, mov2: Movement, movements: List[Movement]
    ) -> Tuple[Movement, Movement]:
        """Apply KungFu Chess collision rules to decide which movement survives.

        A piece jumping in place beats an enemy that moves onto it. Otherwise
        the earlier mover is established on the square: an enemy arriving later
        captures it, while a friendly arriving later is the one that gives way.
        """
        jumper = self._enemy_jumper(mov1, mov2)
        if jumper is not None:
            return jumper, (mov2 if jumper is mov1 else mov1)

        early, late = self._order_by_start(mov1, mov2, movements)
        if early.piece.color != late.piece.color:
            return late, early
        return early, late

    def _enemy_jumper(self, mov1: Movement, mov2: Movement) -> Optional[Movement]:
        """Return whichever movement is a jump in place against a moving enemy, if either is."""
        if mov1.piece.color == mov2.piece.color:
            return None
        is_mov1_jump = (mov1.frm == mov1.to)
        is_mov2_jump = (mov2.frm == mov2.to)
        if is_mov1_jump and not is_mov2_jump:
            return mov1
        if is_mov2_jump and not is_mov1_jump:
            return mov2
        return None

    def _order_by_start(
        self, mov1: Movement, mov2: Movement, movements: List[Movement]
    ) -> Tuple[Movement, Movement]:
        """Return (earlier, later) by start time, breaking ties by issue order in *movements*."""
        if mov1.start_ms != mov2.start_ms:
            return (mov1, mov2) if mov1.start_ms < mov2.start_ms else (mov2, mov1)
        idx1 = movements.index(mov1)
        idx2 = movements.index(mov2)
        return (mov1, mov2) if idx1 < idx2 else (mov2, mov1)

    def _apply_capture(
        self,
        board: BoardInterface,
        state: GameState,
        loser: Movement,
        winner: Movement,
        collision_pos: Position,
        t: int,
    ) -> None:
        """Remove the captured piece from the board and end the game if it was a king.

        *collision_pos* is the square the two pieces met on, which is where the
        capture is visible — not loser.frm, which is only where the board still
        recorded the loser while it was in transit.
        """
        board.set_piece(loser.frm, None)
        loser.piece.transition_to_idle()
        self._clear_selection_at(state, loser.frm)
        self._event_bus.publish(PieceCapturedEvent(
            at_ms=t,
            color=loser.piece.color,
            piece_type=loser.piece.piece_type,
            pos=collision_pos,
            captor_color=winner.piece.color,
            captor_piece_type=winner.piece.piece_type,
            captor_frm=winner.frm,
            captor_to=winner.to,
        ))
        if loser.piece.piece_type in self._config.king_pieces:
            if state.end_game(consts.GAME_OVER_KING_CAPTURED, winner.piece.color):
                self._event_bus.publish(GameEndedEvent(
                    at_ms=t,
                    reason=consts.GAME_OVER_KING_CAPTURED,
                    winner=winner.piece.color,
                ))

    def _abort_into_cooldown(
        self, board: BoardInterface, state: GameState, loser: Movement, stuck_pos: Position, t: int
    ) -> None:
        """Abort a friendly loser's move, parking it where it had actually reached.

        The piece stops at *stuck_pos* rather than snapping back to its origin,
        so a piece that gave way still counts as having travelled.
        """
        if board.get_piece(loser.frm) is loser.piece and stuck_pos != loser.frm:
            board.set_piece(loser.frm, None)
            board.set_piece(stuck_pos, loser.piece)
        loser.piece.transition_to_cooldown()
        state.active_cooldowns.append(
            Cooldown(piece=loser.piece, end_ms=t + self._config.cooldown_duration_ms)
        )
        self._clear_selection_at(state, loser.frm)
        self._event_bus.publish(MoveAbortedEvent(
            at_ms=t,
            color=loser.piece.color,
            piece_type=loser.piece.piece_type,
            frm=loser.frm,
            stopped_at=stuck_pos,
            reason=ABORT_REASON_FRIENDLY_COLLISION,
        ))

    def _clear_selection_at(self, state: GameState, pos: Position) -> None:
        if state.selected_pos == pos:
            state.selected_pos = None
