"""Controller — click interpretation (Layer 6 input).

Owns: translating click events into game commands, the selected-piece
state machine (first click selects, second click requests a move), and
pixel-to-cell mapping via BoardMapper.
Must not own: chess legality, Board mutation, or rendering.

The Controller decides *what* to ask the engine to do; it never decides
*whether* a move is legal. It must not call Board.move_piece directly and
must not call RuleEngine directly — GameEngine.request_move is the only
game-command entry point it uses.
"""

from typing import Optional

from kungfu_chess.model.position import Position
from kungfu_chess.input.board_mapper import BoardMapper
from kungfu_chess.engine.engine_interfaces import BoardRepositoryInterface


class Controller:
    """Interprets raw pixel clicks, maintains selection state, and issues
    move requests to the GameEngine.

    Selection policy (fixed so tests remain simple):
      - No selection + click on a selectable piece -> select it.
      - No selection + click elsewhere (on-board) -> ignored.
      - No selection + click outside the board -> ignored.
      - Selection + on-board click -> GameEngine.request_move(selected,
        target) is called, then the selection is always cleared,
        regardless of whether the move turns out to be legal.
      - Selection + outside-board click -> selection is cancelled and no
        command is sent to GameEngine.
    """

    def __init__(
        self,
        board_mapper: BoardMapper,
        board_repo: BoardRepositoryInterface,
        game_engine: 'GameEngine',  # type: ignore[name-defined]
    ) -> None:
        self._board_mapper = board_mapper
        self._board_repo = board_repo
        self._game_engine = game_engine
        self._selected: Optional[Position] = None

    def on_click(self, x: int, y: int) -> None:
        """Handle a raw pixel click, driving the selection state machine."""
        board = self._board_repo.get_board()
        if board is None:
            return

        target = self._board_mapper.pixel_to_position(x, y, board)

        if self._selected is None:
            if target is None:
                return
            piece = board.get_piece(target)
            if piece is not None and piece.can_select():
                self._selected = target
            return

        source = self._selected
        self._selected = None
        if target is None:
            return
        self._game_engine.request_move(source, target)
