"""Entry point for the persistence worker role.

One of the roles the single image runs, differing from the others only in which
coroutine it hands to `app_runner` — the same pattern `main_ws.py` and
`main_api.py` already establish.

This role holds no socket and computes no game. It drains the durable
`game.finished` stream into the database in batches, which is what keeps a slow
database from stalling a room over an operation that has nothing to do with the
game: the backlog grows in the broker instead of in the simulation.
"""

from server.presentation.app_runner import run_entry_point, run_worker

if __name__ == "__main__":
    run_entry_point("Kung Fu Chess persistence worker", run_worker)
