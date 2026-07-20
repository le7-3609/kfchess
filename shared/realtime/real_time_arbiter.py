"""Real-time arbiter — re-exports (Layer 4).

Concrete implementations live in:
  - realtime/arbiter_interfaces.py (RealTimeArbiterInterface)
  - realtime/arbiter.py            (RealTimeArbiter — tick-loop orchestration)
  - realtime/collision_resolver.py (CollisionResolver — same-square/crossing collisions)
  - realtime/arrival_resolver.py   (ArrivalResolver — landing, capture, en-passant, promotion)
  - realtime/duration_strategies.py (MovementDurationInterface, InstantMovementDuration,
                                      ChebyshevDistanceDuration)
  - realtime/proxy_board.py         (ProxyBoard)

Must not own: chess legality (validator calls stay in engine/controller),
clicks, rendering, or script parsing.
"""

from shared.realtime.arbiter_interfaces import RealTimeArbiterInterface
from shared.realtime.arbiter import RealTimeArbiter
from shared.realtime.collision_resolver import CollisionResolver
from shared.realtime.arrival_resolver import ArrivalResolver
from shared.realtime.duration_strategies import (
    MovementDurationInterface, InstantMovementDuration, ChebyshevDistanceDuration,
)
from shared.realtime.proxy_board import ProxyBoard
