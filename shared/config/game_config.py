"""Game configuration — timing constants and player setup."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from shared.config import consts


@dataclass
class PlayerConfig:
    color_id: str
    forward_direction: int
    pawn_start_rows: List[int]
    promotion_rank: int


@dataclass
class GameConfig:
    """Centralised game configuration."""
    board_rows: int = consts.DEFAULT_BOARD_ROWS
    board_cols: int = consts.DEFAULT_BOARD_COLS
    cell_size_px: int = consts.DEFAULT_CELL_SIZE_PX
    jump_duration_ms: int = consts.DEFAULT_JUMP_DURATION_MS
    ms_per_square: int = consts.DEFAULT_MS_PER_SQUARE
    cooldown_duration_ms: int = consts.DEFAULT_COOLDOWN_DURATION_MS
    en_passant_duration_ms: int = consts.DEFAULT_EN_PASSANT_DURATION_MS
    halfmoves_for_draw: int = consts.DEFAULT_HALFMOVES_FOR_DRAW
    repetitions_for_draw: int = consts.DEFAULT_REPETITIONS_FOR_DRAW

    players: Dict[str, PlayerConfig] = field(default_factory=lambda: {
        consts.PLAYER_W_COLOR: PlayerConfig(
            color_id=consts.PLAYER_W_COLOR,
            forward_direction=consts.PLAYER_W_FORWARD_DIR,
            pawn_start_rows=consts.PLAYER_W_PAWN_START_ROWS,
            promotion_rank=consts.PLAYER_W_PAWN_PROMOTION_RANK,
        ),
        consts.PLAYER_B_COLOR: PlayerConfig(
            color_id=consts.PLAYER_B_COLOR,
            forward_direction=consts.PLAYER_B_FORWARD_DIR,
            pawn_start_rows=consts.PLAYER_B_PAWN_START_ROWS,
            promotion_rank=consts.PLAYER_B_PAWN_PROMOTION_RANK,
        ),
    })

    jumper_pieces: List[str] = field(default_factory=lambda: list(consts.JUMPER_PIECES))
    king_pieces: List[str]   = field(default_factory=lambda: list(consts.KING_PIECES))
    pawn_pieces: List[str]   = field(default_factory=lambda: list(consts.PAWN_PIECES))

    def get_player(self, color_id: str) -> Optional[PlayerConfig]:
        return self.players.get(color_id)
