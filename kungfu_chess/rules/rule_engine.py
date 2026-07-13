"""Rule engine — re-exports (Layer 3).

Concrete implementations live in:
  - rules/path_checker.py      (PathCheckerInterface, PathChecker)
  - rules/threat_validator.py  (ThreatValidator)
  - rules/endgame_validator.py (EndgameValidator, serialize_board_state)
  - rules/castling_validator.py (CastlingValidator, CastlingDestinations)

Must not own: board mutation, animation, click interpretation, game-over state transitions.
"""

from kungfu_chess.rules.path_checker import PathCheckerInterface, PathChecker
from kungfu_chess.rules.threat_validator import ThreatValidator
from kungfu_chess.rules.endgame_validator import EndgameValidator, serialize_board_state
from kungfu_chess.rules.castling_validator import CastlingValidator, CastlingDestinations
