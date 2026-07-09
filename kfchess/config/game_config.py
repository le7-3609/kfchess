from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class PlayerConfig:
    color_id: str
    forward_direction: int
    pawn_start_rows: List[int]

@dataclass
class GameConfig:
    board_rows: int = 8
    board_cols: int = 8
    cell_size_px: int = 100
    jump_duration_ms: int = 1000
    ms_per_square: int = 500
    cooldown_duration_ms: int = 1000
    
    players: Dict[str, PlayerConfig] = field(default_factory=lambda: {
        "w": PlayerConfig(color_id="w", forward_direction=-1, pawn_start_rows=[6]),
        "b": PlayerConfig(color_id="b", forward_direction=1, pawn_start_rows=[1]),
    })

    jumper_pieces: List[str] = field(default_factory=lambda: ["N"])
    king_pieces: List[str] = field(default_factory=lambda: ["K"])
    pawn_pieces: List[str] = field(default_factory=lambda: ["P"])

    def get_player(self, color_id: str) -> Optional[PlayerConfig]:
        return self.players.get(color_id)
