"""Jump command handling (Layer 5).

Owns: turning a jump target into a jump-in-place Movement (used both for the
top-level "jump" command and for a click on an already-selected piece).
"""

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.game_state import GameState, Movement
from kungfu_chess.engine.engine_interfaces import GameStateRepositoryInterface


class JumpCommandProcessor:
    """Executes jump-in-place moves."""

    def __init__(
        self,
        config: 'GameConfig',  # type: ignore[name-defined]
        state_repo: GameStateRepositoryInterface,
    ) -> None:
        self._config = config
        self._state_repo = state_repo

    def execute_active_jump(self, state: GameState, board: BoardInterface, target: Position) -> None:
        piece = board.get_piece(target)
        if piece is None:
            return
        if not piece.can_move():
            return

        arrival_ms = state.clock_ms + self._config.jump_duration_ms
        mov = Movement(
            frm=target,
            to=target,
            piece=piece,
            start_ms=state.clock_ms,
            arrival_ms=arrival_ms,
        )
        state.active_movements.append(mov)
        piece.transition_to_jumping()
        if state.selected_pos == target:
            state.selected_pos = None
        self._state_repo.save_state(state)
