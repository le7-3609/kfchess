# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt      # Pillow, pytest, pre-commit

python client/main_gui.py            # Tkinter GUI (prompts for player names, then plays)
python main.py                       # headless: reads a command script from stdin

python main_ws.py                    # WebSocket game server  (8765, probes on 8080)
python main_api.py                   # HTTP API + probes      (port 8080)
python main_worker.py                # persistence worker     (probes on 8080)
python main_server.py                # ws + api in one process — local development only

docker compose up --build            # whole stack: both roles, Postgres, Redis, NATS
docker compose up --scale ws=2       # two game replicas over one player population
alembic upgrade head                 # apply the schema (KFCHESS_POSTGRES_DSN must be set)

pytest                               # full suite (~860 tests, ~3 minutes); infra tests skip
pytest tests/unit/test_board.py                      # one file
pytest tests/unit/test_board.py::TestBoard::test_x   # one test
pytest -k collision                                  # by name
pytest tests/infra                                   # needs real services — see below

pre-commit run --all-files           # the only hook is pytest; CI (.github/workflows/tests.yml) runs pytest on Python 3.10, builds the image, and runs tests/integration against the Compose stack
```

`KFCHESS_TOKEN_SIGNING_KEY` must be set and **identical** for both roles — the API tier signs session tokens with it and the WebSocket tier verifies them. `KFCHESS_PREVIOUS_TOKEN_KEYS` (comma-separated) keeps tokens issued under an outgoing key valid during a rotation, and `KFCHESS_TRUSTED_PROXIES` names the ingress addresses whose `X-Forwarded-For` may be believed. See `.env.example`.

**Every backing store has two implementations behind one port**, and which one runs is decided entirely by whether its URL is set: `KFCHESS_REDIS_URL` (matchmaking queue, seat/room directory, ownership leases), `KFCHESS_POSTGRES_DSN` (selects `PostgresDatabase` over SQLite), `KFCHESS_NATS_URL` (room events and the finished-game stream). Unset means in-process, which is a real configuration and not a fallback — it is what a single server runs and what keeps `pytest` green with no containers. `tests/infra/` is marked `infra` and skips itself unless the matching variable is set; those tests are what actually prove the Redis/PostgreSQL/NATS implementations satisfy the contract, since an in-memory dict agrees with any contract including a wrong one.

There is no linter or formatter configured. `pytest.ini` exists only to register the `infra` marker and pin the asyncio fixture loop scope; discovery is still pytest's default over `tests/`.

Kubernetes manifests for every role are in [deploy/kubernetes/](deploy/kubernetes/), applied in filename order, with the Alembic job gating the rest. [deploy/prometheus/prometheus.yml](deploy/prometheus/prometheus.yml) is the Compose-topology scrape config; the Kubernetes one is inlined in `80-prometheus.yaml`.

## What the game is

Kung Fu Chess is real-time chess with **no turns**: both colors move concurrently, pieces travel square-by-square over time rather than teleporting, and after arriving a piece sits in cooldown before it can move again. The rules that most often get broken by a careless change (documented in full in [README.md](README.md) and [.agents/AGENTS.md](.agents/AGENTS.md)):

- **Collisions**: two pieces on the same square, or crossing paths (swapping adjacent cells), must resolve. Priority is: an airborne (jumping) piece beats a non-jumping enemy; otherwise the piece that started moving *earlier* wins; ties break by list order/index. Enemy collisions capture; friendly collisions abort the loser into cooldown.
- **Airborne pieces** ignore path blocking and normal path collisions, and capture any enemy that tries to land on their square mid-jump.
- **Clock never flows backward**; guard division-by-zero in duration/interpolation math; don't let a moving piece's vacated origin square cause phantom deletions.

## Architecture

The repo is split into three top-level packages (repo root must be on `sys.path` — running from the root, as pytest and the entry scripts do, handles this):

- **[core/](core/)** — the core game engine and domain models. No UI or network dependency; both other packages import from it.
- **[client/](client/)** — the Tk/Pillow GUI (`client/ui/`, `client/main_gui.py`) and, later, the WebSocket network client.
- **[server/](server/)** — the WebSocket server, matchmaking, auth, persistence, and game rooms. Imports `core` only; never `client`.

Dependencies point one way: `client → core ← server`. Never import `client` or `server` from `core`.

Within `core/`, Clean Architecture with a strict numbered layering. **Every module's docstring names its layer and states what it must *not* own** — read it before editing; those constraints are the design, not decoration. Dependencies point inward only, and interfaces are declared in the inner layer that needs them (e.g. `PixelMapperInterface` lives in `engine/engine_interfaces.py`, not in `input/`, so engine never imports input). Positions are grid `Position(row, col)` throughout every layer from `engine/` inward; pixels exist only at the UI's own boundary (`core/input/board_mapper.py`), which converts to `Position` before anything reaches `GameService`/`GameEngine`.

| Layer | Package | Role |
|---|---|---|
| 1 | [events.py](core/events.py) | `Event` value types, `Observer`, `EventBus` — the pub/sub spine every layer may import |
| 1–2 | [model/](core/model/), [config/](core/config/) | `Position`, `ArrayBoard`, `TextPiece`, `GameState`, `Movement`, `Cooldown`, `Result`; `GameConfig` timing constants |
| 2–3 | [rules/](core/rules/) | pure legality/math: per-piece validators, `PathChecker`, `ThreatValidator`, `EndgameValidator`, `CastlingValidator` |
| 4 | [realtime/](core/realtime/) | `RealTimeArbiter` tick loop + `CollisionResolver`, `ArrivalResolver`, `ProxyBoard`, duration strategies |
| 5 | [engine/](core/engine/) | `GameEngine` command dispatch, click/jump/castling processors, game-over detection |
| 6 | [input/](core/input/), [view/](core/view/), [ui/](client/ui/) | pixel↔cell mapping, `GameSnapshot` DTO, Pillow renderer, Tk window (`ui/` lives in `client/`) |
| 7 | [io/](core/io/) | board parse/print, moves log, JSON history store, replay decorator |
| 8–9 | [texttests/](core/texttests/), [runtime/](core/runtime/) | `.kfc` script runner; asyncio tick loop |

The core is a pure simulation engine with no UI or network dependency. Keep it that way — the server consumes it through `GameService`/`bootstrap` without touching `rules/`, `realtime/`, or `engine/`. Note `input/` stays in `core` (not `client`): `bootstrap.py` wires `BoardMapper` for the pixel-coordinate click path used by text tests and bots.

### The two boundaries that matter

**[service.py](core/service.py) — `GameService` is the only public entry point.** The Tk window, script runner, and bots all talk exclusively to it: commands (`init_game`, `execute_command`, `click`, `right_click`, `advance_clock`, history save/load), queries (`get_snapshot`, `get_moves`, `list_saves`), and event subscription (`subscribe`, `unsubscribe`). Nothing outside reaches through to `GameEngine`, the repositories, the arbiter, or the bus itself. Optional collaborators (`arbiter`, `moves_log`, `history_store`, `event_bus`) gate only the query/history/subscription methods — the pure `execute()` path used by text tests works without them.

**[bootstrap.py](core/bootstrap.py) — the composition root.** All wiring happens here; nothing else constructs the object graph.
- `build_core(...)` returns `CoreComponents`, the shared stack.
- `build_service()` — `InstantMovementDuration`, for tests and scripted runs.
- `build_realtime_service()` — `ChebyshevDistanceDuration`, pieces travel over time; adds moves log + history store.
- Bots need the *same* repo/arbiter instances, so they can't be injected after the fact — [bot_factory.py](core/bot_factory.py) composes `build_core()` with bot construction instead.

### Non-obvious invariants

- **Active motions live in `RealTimeArbiter`, not `GameState`.** Go through `register_motion` / `movements` / `remove_motion`; never mutate a shared list.
- **Selection state (`GameState.selected_pos`) is owned by `GameEngine`.** The `Controller` is a stateless click→command translator, and the Tk window keeps no selection — it arrives back via the snapshot.
- **`ProxyBoard` overlays in-flight motions on demand** (O(active_motions) per lookup) instead of copying the board each simulation step. Use `arbiter.get_effective_board(...)` for "where is everything right now".
- **`GameEngine._resolve_pending` deliberately skips the game-end scan** unless piece positions or cooldown membership changed — the checkmate/stalemate scan is expensive enough to blow the 16ms render budget if run on every idle tick. Don't make it unconditional.
- **The rendering path is one-way**: `advance_clock` → `SnapshotBuilder` builds an immutable `GameSnapshot` → `PillowRenderer` composes an `Img` → tkinter only displays the finished frame. Legal-move highlighting reuses `GameEngine.legal_moves_from` rather than reimplementing rules in the view.
- **Events notify; they never draw.** The engine and resolvers publish `PieceMovedEvent` / `PieceCapturedEvent` / `GameEndedEvent` etc. onto the `EventBus`; `TkGameWindow.on_event` only records view state (capture flashes, scores, a pending game-over prompt) because it runs *mid-tick*, and the next `_refresh()` paints it through `Img`. Events carry plain values, never live `Piece`/`Board` objects. `EventBus.publish` contains subscriber exceptions on purpose — a UI failure must not abandon a half-resolved tick — and dispatch is depth-first, so a derived event (`ScoreUpdatedEvent`) can reach a later subscriber before its cause.
- **`GameService._adjust_pawn_rules_for_board_height`** rewrites pawn start rows/promotion ranks per installed board, since text-test boards are often smaller than 8×8.

### The server's own boundaries

`server/` keeps four layers whose dependencies point one way — `presentation → application → domain ← infrastructure` — and [tests/unit/test_server_layer_boundaries.py](tests/unit/test_server_layer_boundaries.py) asserts it structurally by reading imports. Two things there are easy to get wrong:

- **[app_runner.py](server/presentation/app_runner.py) is the server's composition root**, the counterpart to `core/bootstrap.py`. All CLI parsing, TLS-context construction, token-service and probe wiring happen there; the three entry scripts (`main_ws.py`, `main_api.py`, `main_server.py`) differ only in which coroutine they hand it.
- **A registered room and a held lease are the same set.** `RoomManager` takes an ownership lease before it registers a room — which is why `create_room` is `async`: the id is claimed against the lease store, not merely generated, and a contended candidate is retried. Losing a lease calls `surrender_room`, which reaps the room here rather than letting a second instance compute the same game. Don't add a path that registers a room without a lease, and don't make a lost lease merely log.
- **The wire protocol is event-driven, not snapshot-driven.** `GameRoom._on_tick` deliberately does *not* broadcast the board. A full `game_state` frame goes out in exactly three cases — game start, a spectator joining or reconnect resync, and a low-frequency reconciliation frame — and everything between is `event_*` frames the client interpolates from (`client/network/snapshot_projection.py`). Reintroducing a per-tick broadcast puts ~219 KB/s per room back on the wire against a measured 206 B/s for the event stream. The snapshot is also split per recipient: `selected_pos`/`legal_move_targets`/`castle_targets` belong to the player who selected and must never reach the opponent.

### Text scripts (`.kfc`)

Integration tests in [tests/integration/scripts/](tests/integration/scripts/) are the executable spec for the rules. Format: a `Board:` block of tokens (`wK`, `bR`, `.`), a `Commands:` block (`click ROW COL`, `right_click ROW COL`, `wait MS`, `print board` — grid cell coordinates, unrecognized lines are ignored by design), and an `Expected:` board block. They run through `build_service()` (instant movement) with `require_kings=False`. Adding a rule case here is usually better than a unit test

## Conventions & Clean Code

- **Clean Code & Single Responsibility:** Keep functions short and highly focused. If a function has more than one responsibility, or contains nested complex logic, **you must split it** and extract the logic into private helper functions with clear names.
- **Self-Documenting Code:** Good code is like a book that tells its own story. Prioritize readable code with descriptive variable and function names over comments.
- **Comment Rules (Strict):**
  - **DELETE "What" comments:** Remove inline comments that simply explain *what* the code is doing. Refactor the code to be expressive enough that it doesn't need them.
  - **KEEP "Why" comments:** Preserve and write comments that explain *why* a decision was made (e.g., business logic, non-obvious invariants, or performance hacks).
- **Docstrings:** Always use standard docstrings for modules, classes, and functions to explain their purpose, expected inputs, and outputs. Keep these; they are the correct way to document APIs.
- **Error Handling:** Errors flow back as `Result.ok/fail` (see `model/game_state.py`), not exceptions, on the command path.
- **Testing:** Every feature, fix, or refactor needs test coverage, and the suite must stay green. It exists to pin collision priorities and rule edge cases across refactors.

## Strict Design Principles & Boundaries

- **High-Scale & Loose Coupling:** Architect for scale. Maintain extremely loose coupling between components. Minimize direct dependencies, avoid mixing domains, and prefer composition over inheritance.
- **Strict Boundaries (Horizontal & Vertical):**
  - **Layer Boundaries:** Layers are strictly isolated. Data crossing layers must be properly mapped or validated.
  - **Domain Model Boundaries:** Boundaries are not just between layers, but between classes within the same layer. Inside the Domain Model, every class must have a highly specific responsibility. Do not bleed logic between domain entities.
- **Gatekeeping & Validation (Fail Fast):** 
  - Every class acts as a "gatekeeper" for its own data and invariants. Clearly define which component is responsible for which validation.
  - **Fail Fast:** Validate inputs and state immediately. If something is wrong, fail at the exact point of origin (where the problem was found). Never drag invalid state or errors across layers.
- **Traceability:** The execution flow must be easy to trace and reason about. Avoid hidden side-effects, implicit state mutations, or overly "clever" dynamic metaprogramming that obscures *where* and *why* things happen.

## Conventions

- PEP-8, type annotations, and the existing docstring style: a module docstring stating layer + owns/must-not-own, and comments that explain *why* for real-time simulation subtleties.
- Errors flow back as `Result.ok/fail` (see [model/game_state.py](core/model/game_state.py)), not exceptions, on the command path.
- Every feature, fix, or refactor needs test coverage, and the suite must stay green — it exists to pin collision priorities and rule edge cases across refactors.
