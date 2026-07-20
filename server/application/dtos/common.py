"""Application-layer DTOs and the error vocabulary shared across use cases.

Layer: application (server/application/dtos)
Owns: the data shapes crossing the presentation <-> application boundary, and
the rejection messages more than one use case can produce.
Must not own: wire framing (a use case returns a reason string; presentation
decides it becomes an `error` frame) or domain invariants.
"""

from typing import Tuple

# (user_id, username, elo) as resolved by AuthService.
Identity = Tuple[int, str, int]

# Shared because three separate lobby actions reject on the same condition;
# defining it once keeps the wording from drifting apart between them.
ERROR_ALREADY_SEATED = "Already seated in a room"
