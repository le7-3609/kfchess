"""Bot move-selection strategies (Layer 6 / Input).

Owns: the *policy* of which legal move a bot plays — random, greedy capture,
and (in the client) an LLM. A strategy proposes; it never decides legality.
Must not own: legal-move generation (that is the rules layer's, reached only
through the list handed in), the game clock, command dispatch, or pacing.

The interface is declared here, in the layer that consumes strategies, so the
dependency points inward the same way `PixelMapperInterface` lives with the
engine that needs it, not with the UI that implements it. An LLM strategy
implemented in `client/` therefore imports this contract from `shared`, keeping
the arrow `client -> shared`.
"""

import random
from typing import List, Optional, Protocol, Tuple

from shared.config import consts
from shared.model.board import BoardInterface
from shared.model.game_state import GameState
from shared.model.position import Position

Move = Tuple[Position, Position]


class BotStrategyInterface(Protocol):
    """Chooses one move from a pre-validated set of legal moves.

    The caller (StrategyBotInputSource) has already produced *legal_moves* from
    the rules layer and resolved *board* to the effective board those moves were
    generated against. A strategy must return one of *legal_moves* or None; an
    out-of-set answer is discarded by the caller and never executed.
    """

    def choose_move(
        self, legal_moves: List[Move], board: BoardInterface, state: GameState
    ) -> Optional[Move]:
        ...


class RandomMoveStrategy:
    """Picks a uniformly random legal move — the baseline opponent."""

    def choose_move(
        self, legal_moves: List[Move], board: BoardInterface, state: GameState
    ) -> Optional[Move]:
        if not legal_moves:
            return None
        return random.choice(legal_moves)


class GreedyCaptureStrategy:
    """Grabs the most valuable enemy piece it can, else moves at random.

    Pure function of its inputs: each move scores the value of whatever sits on
    its target square (0 for a quiet move), the best score wins, and ties —
    including the all-quiet case — break randomly so the bot does not loop
    deterministically in a position with nothing to take.
    """

    def choose_move(
        self, legal_moves: List[Move], board: BoardInterface, state: GameState
    ) -> Optional[Move]:
        if not legal_moves:
            return None

        best_score = max(self._capture_value(board, target) for _src, target in legal_moves)
        best_moves = [
            move for move in legal_moves if self._capture_value(board, move[1]) == best_score
        ]
        return random.choice(best_moves)

    def _capture_value(self, board: BoardInterface, target: Position) -> int:
        """Material worth of capturing whatever occupies *target* (0 if empty).

        Legal moves only ever target an empty square or an enemy piece, so the
        occupant's colour needs no check here. The king is scored above all
        material because taking it ends the game.
        """
        piece = board.get_piece(target)
        if piece is None:
            return 0
        if piece.piece_type == consts.PIECE_KING:
            return consts.BOT_KING_CAPTURE_VALUE
        return consts.PIECE_VALUES.get(piece.piece_type, 0)
