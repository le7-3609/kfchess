"""Bot strategy input source (Layer 5/Input).

Acts as an automated player issuing commands to the engine.
Belongs in the input layer as it simulates user input without
violating the rules or board layers.
"""

import random
from typing import List, Tuple

from kungfu_chess.model.position import Position
from kungfu_chess.rules.piece_rules import MoveValidatorFactoryInterface
from kungfu_chess.rules.rule_engine import PathCheckerInterface, ThreatValidator
from kungfu_chess.realtime.real_time_arbiter import RealTimeArbiterInterface
from kungfu_chess.engine.game_engine import BoardRepositoryInterface, GameStateRepositoryInterface


class RandomBotInputSource:
    """A bot that selects a random legal move for its color."""

    def __init__(
        self,
        color: str,
        board_repo: BoardRepositoryInterface,
        state_repo: GameStateRepositoryInterface,
        move_validator_factory: MoveValidatorFactoryInterface,
        path_checker: PathCheckerInterface,
        threat_validator: ThreatValidator,
        arbiter: RealTimeArbiterInterface,
        config,
    ) -> None:
        self._color = color
        self._board_repo = board_repo
        self._state_repo = state_repo
        self._move_validator_factory = move_validator_factory
        self._path_checker = path_checker
        self._threat_validator = threat_validator
        self._arbiter = arbiter
        self._config = config

    def get_next_commands(self) -> List[str]:
        """Generate click commands for a random valid move."""
        board = self._board_repo.get_board()
        if board is None:
            return []
        state = self._state_repo.get_state()
        if state.game_over:
            return []

        eff_board = self._arbiter.get_effective_board(board, state, state.clock_ms)
        en_passant_targets = [ep.pos for ep in state.en_passant_targets]

        valid_moves: List[Tuple[Position, Position]] = []

        for r in range(eff_board.rows):
            for c in range(eff_board.cols):
                pos = Position(r, c)
                piece = eff_board.get_piece(pos)
                if piece is None or piece.color != self._color:
                    continue
                if not piece.can_move():
                    continue

                validator = self._move_validator_factory.get_validator(piece.piece_type)
                for tr in range(eff_board.rows):
                    for tc in range(eff_board.cols):
                        target = Position(tr, tc)
                        if pos == target:
                            continue

                        if not validator.is_legal(pos, target, self._color, eff_board.rows):
                            continue
                        if not self._path_checker.is_path_clear(eff_board, pos, target):
                            continue
                        if not self._path_checker.can_land(eff_board, piece, pos, target, en_passant_targets):
                            continue

                        original_target_piece = eff_board.get_piece(target)
                        eff_board.set_piece(pos, None)
                        eff_board.set_piece(target, piece)
                        is_threatened = self._threat_validator.is_king_threatened(eff_board, self._color)
                        eff_board.set_piece(pos, piece)
                        eff_board.set_piece(target, original_target_piece)

                        if not is_threatened:
                            valid_moves.append((pos, target))

        if not valid_moves:
            return []

        src, dst = random.choice(valid_moves)

        cell_size = self._config.cell_size_px
        src_x, src_y = src.col * cell_size, src.row * cell_size
        dst_x, dst_y = dst.col * cell_size, dst.row * cell_size

        return [
            f"click {src_x} {src_y}",
            f"click {dst_x} {dst_y}"
        ]
