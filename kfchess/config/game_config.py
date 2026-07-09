from dataclasses import dataclass, field
from typing import Dict, List, Optional
import consts

@dataclass
class PlayerConfig:
    color_id: str
    forward_direction: int
    pawn_start_rows: List[int]

@dataclass
class GameConfig:
    board_rows: int = consts.DEFAULT_BOARD_ROWS
    board_cols: int = consts.DEFAULT_BOARD_COLS
    cell_size_px: int = consts.DEFAULT_CELL_SIZE_PX
    jump_duration_ms: int = consts.DEFAULT_JUMP_DURATION_MS
    ms_per_square: int = consts.DEFAULT_MS_PER_SQUARE
    cooldown_duration_ms: int = consts.DEFAULT_COOLDOWN_DURATION_MS
    en_passant_duration_ms: int = consts.DEFAULT_EN_PASSANT_DURATION_MS
    
    players: Dict[str, PlayerConfig] = field(default_factory=lambda: {
        "w": PlayerConfig(color_id="w", forward_direction=-1, pawn_start_rows=[6]),
        "b": PlayerConfig(color_id="b", forward_direction=1, pawn_start_rows=[1]),
    })

    jumper_pieces: List[str] = field(default_factory=lambda: list(consts.JUMPER_PIECES))
    king_pieces: List[str] = field(default_factory=lambda: list(consts.KING_PIECES))
    pawn_pieces: List[str] = field(default_factory=lambda: list(consts.PAWN_PIECES))

    def get_player(self, color_id: str) -> Optional[PlayerConfig]:
        return self.players.get(color_id)
