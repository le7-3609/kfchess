"""Snapshot builder — turns Board/GameState/Arbiter into a GameSnapshot (Layer 6 boundary).

Owns: reading BoardInterface + GameState + RealTimeArbiterInterface (all
read-only) and packaging them into the immutable GameSnapshot DTO the
Renderer consumes.
Must not own: game rules, board mutation, input parsing, or timing
advancement. Legal-move/castle-target queries are delegated to GameEngine's
read-only query methods rather than duplicated here.
"""

from typing import Dict, Optional

from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.game_state import GameState
from kungfu_chess.model.position import Position
from kungfu_chess.engine.game_engine import GameEngine
from kungfu_chess.realtime.arbiter_interfaces import RealTimeArbiterInterface
from kungfu_chess.view.game_snapshot import GameSnapshot, MovementSnapshot, PieceSnapshot
from kungfu_chess.view.piece_visual_state import PieceVisualState


class SnapshotBuilder:
    """Builds a GameSnapshot for the current board/state, once per render frame."""

    def __init__(
        self,
        engine: GameEngine,
        arbiter: RealTimeArbiterInterface,
        config: 'GameConfig',  # type: ignore[name-defined]
    ) -> None:
        self._engine = engine
        self._arbiter = arbiter
        self._config = config

    def build(self, board: BoardInterface, state: GameState) -> GameSnapshot:
        clock_ms = state.clock_ms

        moving_positions = {mov.frm for mov in self._arbiter.movements()}
        cooldown_by_piece = {id(c.piece): c for c in state.active_cooldowns}

        pieces: Dict[Position, PieceSnapshot] = {}
        for r in range(board.rows):
            for c in range(board.cols):
                pos = Position(r, c)
                piece = board.get_piece(pos)
                if piece is None or pos in moving_positions:
                    continue
                pieces[pos] = self._static_piece_snapshot(piece, cooldown_by_piece, clock_ms)

        active_movements = []
        for mov in self._arbiter.movements():
            piece_snapshot = self._moving_piece_snapshot(mov, clock_ms)
            pieces[mov.frm] = piece_snapshot
            active_movements.append(
                MovementSnapshot(
                    frm=mov.frm, to=mov.to, piece=piece_snapshot, start_ms=mov.start_ms, arrival_ms=mov.arrival_ms
                )
            )

        cooldown_positions = tuple(
            pos for pos in (self._find_cooldown_pos(board, c) for c in state.active_cooldowns) if pos is not None
        )

        legal_move_targets: tuple = ()
        castle_targets: tuple = ()
        if state.selected_pos is not None:
            legal_move_targets = tuple(self._engine.legal_moves_from(state.selected_pos))
            castle_targets = tuple(self._engine.castle_rook_targets_from(state.selected_pos))

        return GameSnapshot(
            rows=board.rows,
            cols=board.cols,
            pieces=pieces,
            selected_pos=state.selected_pos,
            legal_move_targets=legal_move_targets,
            castle_targets=castle_targets,
            active_movements=tuple(active_movements),
            cooldown_positions=cooldown_positions,
            clock_ms=clock_ms,
            game_over=state.game_over,
            game_over_reason=state.game_over_reason,
            winner=state.winner,
        )

    # -- internals ----------------------------------------------------------

    def _static_piece_snapshot(self, piece, cooldown_by_piece, clock_ms: int) -> PieceSnapshot:
        cooldown = cooldown_by_piece.get(id(piece))
        if cooldown is not None:
            duration = self._config.cooldown_duration_ms
            remaining = max(0, cooldown.end_ms - clock_ms)
            elapsed = max(0, duration - remaining)
            state = PieceVisualState.SHORT_REST
        else:
            state, elapsed, duration = PieceVisualState.IDLE, 0, 0

        return PieceSnapshot(
            color=piece.color,
            piece_type=piece.piece_type,
            has_moved=piece.has_moved,
            can_select=piece.can_select(),
            can_move=piece.can_move(),
            state=state,
            state_elapsed_millis=elapsed,
            state_duration_millis=duration,
        )

    def _moving_piece_snapshot(self, mov, clock_ms: int) -> PieceSnapshot:
        elapsed = max(0, clock_ms - mov.start_ms)
        duration = max(0, mov.arrival_ms - mov.start_ms)
        # Jump-in-place (activated by clicking an already-selected piece) is the only
        # true "airborne" visual; a knight's ordinary move still renders as MOVE even
        # though the arbiter treats it as positionally airborne mid-flight.
        visual_state = PieceVisualState.JUMP if mov.frm == mov.to else PieceVisualState.MOVE

        return PieceSnapshot(
            color=mov.piece.color,
            piece_type=mov.piece.piece_type,
            has_moved=mov.piece.has_moved,
            can_select=False,
            can_move=False,
            state=visual_state,
            state_elapsed_millis=elapsed,
            state_duration_millis=duration,
        )

    def _find_cooldown_pos(self, board: BoardInterface, cooldown) -> Optional[Position]:
        return board.find_position(cooldown.piece)
