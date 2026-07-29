# Kung Fu Chess

Kung Fu Chess is a real-time, no-turns variation of traditional chess. Unlike classical chess, any piece can be moved at any time, adding a fast-paced action element to the profound strategy of the original game.

---
## Game Rules (What makes a legal Kung Fu Chess game?)

The core gameplay centers on continuous action where players do not wait for turns. The rules of this simulation are defined as follows:

* **Real-Time (No Turns)**: There are no player turns. Both players ("w" and "b") can move pieces concurrently at any clock time.
* **Travel Duration**: Pieces do not teleport. They travel square-by-square over time. The travel duration is defined by their speed configurations (e.g., using Chebyshev distance).
* **Collisions**:
  * **Same-Square Collision**: Two pieces arriving or occupying the same square at the same time must resolve collisions (winner lands/captures, loser is captured or aborted/returned).
  * **Crossing Collision**: If two pieces cross paths (swap adjacent cells), they must collide and resolve.
  * **Resolution Priority**: A jumping (airborne) piece wins against a non-jumping piece of the opponent. Otherwise, the piece that started moving earlier wins. If they started at the same time, break ties using their list order or index.
* **Jumping (Airborne state)**:
  * A jumping piece (e.g., Knight, or piece jumping in-place) is "airborne."
  * It is immune to intermediate path blocking and regular path collisions while in the air.
  * If an enemy piece attempts to land on the jumping piece's square while it is airborne, the jumping piece captures the landing piece.
* **Cooldown**: After a piece arrives at its destination or completes a jump, it enters a cooldown period. During this time, the piece cannot be selected or moved. The cooldown duration is configurable (e.g., via `cooldown_duration_ms`).

---
## Architecture and Design Patterns

The codebase is built on **Clean Architecture** and follows **SOLID** design principles. The repository is split into three top-level packages with dependencies pointing one way — `client → core ← server` — so the core game engine never imports UI or network code:

* **`core/`** — the core game engine and domain models: `Event`/`EventBus` pub-sub, the `model`/`config` layer (`Position`, `ArrayBoard`, `TextPiece`, `GameState`, `Movement`, `Cooldown`, `Result`, `GameConfig`), pure `rules` (piece validators, `PathChecker`, `ThreatValidator`, `EndgameValidator`, `CastlingValidator`), the `realtime` tick loop (`RealTimeArbiter`, `CollisionResolver`, `ArrivalResolver`, `ProxyBoard`), the `engine` command dispatcher (`GameEngine`), the `input`/`view` DTO and pixel-mapping layer, and `io` (board parsing/printing, moves log, JSON history store, replay). No UI or network dependency lives here.
* **`client/`** — the Tkinter/Pillow multiplayer GUI. `client/auth/cli_auth.py` runs a terminal login/register handshake against the server before any window opens; `client/network/network_client.py` then owns a background-thread WebSocket connection for the game session itself; `client/main_gui.py` wires both into `client/ui/window/game_window.py`, which renders the board and turns clicks into algebraic-notation move requests (`client/notation/algebraic_notation.py`, `client/network/network_snapshot_decoder.py`).
* **`server/`** — the asyncio WebSocket server and HTTP API, in four layers whose dependencies point one way (`presentation → application → domain ← infrastructure`, asserted structurally by `tests/unit/test_server_layer_boundaries.py`): connection routing and move relay, authentication (`auth_service.py`, `token_service.py`, `database.py`), matchmaking/ELO (`queue.py`, `elo.py`), rooms and persistence, reconnection (`heartbeat.py`, `disconnect_handler.py`), and observability (`observability/`). See [Multiplayer Server](#multiplayer-server). Imports `core` only, never `client`.

Two boundaries hold the whole system together:

* **`core/service.py` — `GameService`** is the *only* public entry point into the engine. The Tk window, the text-script runner, bots, and the server's game rooms all talk exclusively to it (`init_game`, `execute_command`, `click`, `right_click`, `advance_clock`, history save/load, `get_snapshot`, `get_moves`, event `subscribe`/`unsubscribe`) — nothing reaches through to `GameEngine`, the repositories, the arbiter, or the event bus directly.
* **`core/bootstrap.py`** is the composition root. `build_service()` wires an instant-movement engine for tests and scripts; `build_realtime_service()` wires the real-time (`ChebyshevDistanceDuration`) engine plus moves log and history store used by the GUI and server.

This strict separation means the storage, interface, or network layer can be swapped without touching the rules or core simulation logic.

---
## Multiplayer Server

`server/` is an asyncio-based WebSocket server (built on `websockets`) that turns the local simulation into a networked multiplayer game. It runs as **two roles built from one image**, because they have almost nothing in common operationally — restarting the socket tier disconnects every player mid-game, while restarting the API tier costs nothing:

* **`main_ws.py` — the game socket** (`ws_server.py`, `game_room.py`, `room_manager.py`). Every connection opens with a mandatory `auth` handshake (password, or a token issued by the API tier), then a second frame routes it: `play` enters ELO-bounded matchmaking, `create_room`/`join_room` handle named rooms, `reconnect` rebinds an existing seat. A filled room builds a real `GameService` + `AsyncGameRunner` (real-time, `ChebyshevDistanceDuration`) and ticks at 20 Hz. A queue timeout seats a bot instead of dead-ending the player; further joiners spectate.
* **`main_api.py` — the HTTP API** (`http_api.py`): completed-game replay and PGN export, the leaderboard, `POST /api/auth/login` and `/api/auth/refresh` for session tokens, plus `/healthz`, `/readyz` and `/metrics`. Replay responses are immutable and carry an `ETag` with a one-year `Cache-Control`, so a CDN can serve every repeat view without touching the database.
* **`main_server.py`** runs both on one loop, for local development only.

The wire protocol is **event-driven, not snapshot-driven**. Because pieces travel over time rather than teleporting, a client is told when a move *starts* and how long it will take (`event_move_started` carries `from`, `to`, `arrival_ms`, `at_ms`) and interpolates the travel itself (`client/network/snapshot_projection.py`). A full board is sent in three cases only: game start, a spectator joining or a reconnect resync, and a low-frequency reconciliation frame that repairs any drift. Measured over a real socket for a two-player room, that is **206 B/s** for the event stream against **219,200 B/s** for the snapshot-per-tick design it replaced. The snapshot is also serialized per recipient, so one player's selection and highlighted squares never reach their opponent.

Supporting behaviour, all wired: bcrypt-hashed accounts in SQLite (`auth_service.py`, `database.py`) at a pinned cost factor, hashed off the event loop so a login cannot stall the game tick; ±100 ELO matchmaking with a 60-second timeout (`queue.py`, `elo.py`); ping/pong heartbeats that close a half-open socket (`heartbeat.py`) and a 30-second reconnection window that resyncs a returning player (`disconnect_handler.py`); rate limits on connections, frames, room creation and HTTP requests; and idempotent writes — a repeated `move_id` is answered from cache, and re-saving a finished game is a no-op rather than a duplicate or an error.

[Server_Design.md](Server_Design.md) is the ordered work plan for scaling this to a fleet. Steps 1–6 are implemented — deployability, the protocol, edge security, the role split, shared state in Redis and PostgreSQL, and room events over NATS. Step 7 is implemented as far as ownership goes: a room is registered only once this instance holds its Redis lease, a lost lease tears the room down here rather than letting a second authority compute it, and a drain refuses new rooms while renewing the ones already running — but the gateway and the game authority are still one process rather than two. Step 8 has its Kubernetes manifests and persistence worker, and lacks the metrics adapter its HPAs need.

---
## Technological Stack & Current Status

* **Language**: Python 3 (CI runs on 3.10).
* **Multiplayer client**: `client/main_gui.py` is a networked client — it authenticates over the terminal (`client/auth/cli_auth.py`), then opens a Tkinter window that plays a real-time game over WebSocket against a running `main_server.py`. It no longer runs a self-contained local game; the local, server-less path (`core/service.py`'s `GameService`) still backs `main.py`, the `.kfc` text-script runner, and bots.
* **Multiplayer server**: two roles from one image — `main_ws.py` (game sockets and simulation) and `main_api.py` (history, leaderboard, token issuance, health and metrics endpoints), with `main_server.py` running both for local development. Accounts, ELO matchmaking, named rooms, spectators, bots, reconnection, rate limiting and game-history persistence are all live (see [Multiplayer Server](#multiplayer-server)).
* **Deployment**: a `Dockerfile` and `docker-compose.yml` bring both roles up on one machine; CI runs the test suite, builds the image tagged with the commit SHA, and runs the integration suite against the Compose stack.

---
## Graphical UI

The game ships with a networked graphical client built with **Tkinter** (window/canvas/input) and **Pillow** (piece rendering); it plays against `main_server.py` over WebSocket rather than simulating locally.

* **Login**: on launch, the terminal prompts for login or registration (username/password) and performs the auth handshake against the server before any window opens; the process exits if authentication fails.
* **Joining a game**: after authenticating, the client opens the Tk window and connects the persistent game socket, which auto-joins the server's single shared room — the window shows a "Waiting for opponent..." placeholder until a second player connects, then displays both names and the assigned color.
* **Board**: an 8x8 board is drawn on a canvas, redrawn from each `GameSnapshot` the server broadcasts (on every tick and after every accepted move).
* **Selecting and moving**: left-click a piece, then left-click a destination square to send an algebraic-notation move request (e.g. `e2`→`e4`) to the server. The server is the sole authority on legality; an illegal pick is simply rejected there. Right-click "jump in place" is not exposed in the networked window.
* **Real-time movement**: since Kung Fu Chess has no turns, pieces animate traveling across the board over time, and both players can queue moves independently and concurrently while the server's clock keeps advancing.
* **Preferences**: piece theme (Classic, Minimal, Modern, My Pieces, Pieces) and board color theme (Original, Classic, Green, Blue, Dark) are configurable from the Settings menu and persisted to disk between sessions via `UserSettingsStore`. Movement speed, cooldown duration, and save/load history are local-`GameService` settings and aren't exposed here — there's no local service to configure once play is server-driven.

### Running the app with the UI

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the server (see [Running the multiplayer server](#running-the-multiplayer-server) below) — the client needs one to connect to.
3. Launch the graphical client:
   ```bash
   python client/main_gui.py [server_url]
   ```
   `server_url` defaults to `ws://localhost:8765`. This prompts for login/register credentials in the terminal, then opens a Tkinter window connected to that game session — the room fills as soon as a second player connects.

A non-graphical entry point is also available via `python main.py`, which reads a command script from stdin (`click`, `right_click`, `wait`, `print board`, ...) and drives the local `GameService` engine directly, without any network layer or the Tkinter/Pillow UI.

### Running the multiplayer server

Each role takes the same flags; they differ in which ports they bind.

```bash
python main_ws.py     [--host HOST] [--port PORT]      [--db-path PATH] [--log-level LEVEL] [--tls-cert CERT --tls-key KEY]
python main_api.py    [--host HOST] [--http-port PORT] [--db-path PATH] [--log-level LEVEL] [--tls-cert CERT --tls-key KEY]
python main_server.py  # both roles on one loop, for local development
```

Defaults are `localhost:8765` for the game socket and `localhost:8080` for the API; `client/main_gui.py` connects to the socket automatically once started.

Set `KFCHESS_TOKEN_SIGNING_KEY` to the **same** value for both roles — the API tier signs session tokens with it, the socket tier verifies them — and see `.env.example` for the key-rotation and trusted-proxy variables. TLS normally terminates at an ingress; the `--tls-cert`/`--tls-key` flags exist so a TLS problem can be reproduced locally without one.

The API tier exposes `/healthz` (liveness — deliberately touches nothing external), `/readyz` (readiness — pings the database, and reports not-ready while draining) and `/metrics` (Prometheus text, including the tick-duration histogram that reveals a simulation falling behind wall-clock time).

### Running the whole stack in Docker

`docker-compose.yml` runs the same roles as separate services — from one image, differing only in their entry command — alongside the backing stores they select: PostgreSQL for durable data, Redis for the matchmaking queue and the seat/room directory, NATS for room events and the finished-game stream.

1. Create the untracked `.env`. Compose declares `KFCHESS_TOKEN_SIGNING_KEY` and `POSTGRES_PASSWORD` as required, so it refuses to start until both are filled in:
   ```bash
   cp .env.example .env
   python -c "import secrets; print(secrets.token_urlsafe(48))"   # -> KFCHESS_TOKEN_SIGNING_KEY
   python -c "import secrets; print(secrets.token_urlsafe(24))"   # -> POSTGRES_PASSWORD
   ```
   Leave the `KFCHESS_REDIS_URL`, `KFCHESS_POSTGRES_DSN` and `KFCHESS_NATS_URL` lines commented out — those point at `localhost` for a host-side run, and Compose sets the container-network values itself.

2. Bring the stack up:
   ```bash
   docker compose up --build
   ```
   Startup order is enforced in the file rather than by waiting: `postgres` becomes healthy, then `migrate` runs `alembic upgrade head` to completion, and only then do `ws` and `api` start — they depend on `service_completed_successfully`, so a replica never races the schema it needs.

3. Connect the client, which reaches the published `ws` port like any other server:
   ```bash
   python client/main_gui.py ws://localhost:8765
   ```

| Service | Host port | What it serves |
|---|---|---|
| `ws` | 8765 | player sockets and the game simulation (`main_ws.py`) |
| `api` | 8080 | history, leaderboard, token issuance, plus `/healthz`, `/readyz`, `/metrics` (`main_api.py`) |
| `migrate` | — | `alembic upgrade head`, run once and exits |
| `postgres` | 5432 | durable data |
| `redis` | 6379 | matchmaking queue, seat/room directory, ownership leases |
| `nats` | 4222, 8222 | room events (core) and finished games (JetStream); monitoring on 8222 |

Useful variants:

```bash
docker compose up --scale ws=2             # two game replicas over one player population
docker compose --profile tools run migrate # re-apply migrations by hand
docker compose logs -f ws                  # follow one role
docker compose down                        # stop; add -v to drop the postgres volume too
```

`--scale ws=2` is what the Redis and NATS wiring is *for*: shared state is the only thing that makes two replicas one player population rather than two disjoint ones, and the `8765-8775` port range (rather than a fixed `8765`) is there so Compose can hand each replica the next free host port instead of failing on a collision. Redis runs with persistence disabled on purpose — everything it holds is rebuilt by players reconnecting and replicas re-registering, so restoring a snapshot would only resurrect stale routing.

Two pieces of the system have no Compose service yet: the persistence worker (`python main_worker.py`) and Prometheus. `deploy/prometheus/prometheus.yml` is written against this topology and can be pointed at it, but nothing in `docker-compose.yml` starts it; the Kubernetes manifests in `deploy/kubernetes/` cover both.

---
## Tests

The project puts a heavy emphasis on reliability and mathematical accuracy, given the edge cases of real-time movement (e.g., division by zero guards, precise collision resolutions).
* **Framework**: `pytest`, plus `pytest-asyncio` for the WebSocket server's async tests.
* **Coverage**: 760+ unit and integration tests across the engine, UI, and server (matchmaking, auth, tokens, ELO, rooms, disconnect handling, rate limiting, health probes, metrics, protocol). The server is exercised both in isolation and end to end: `tests/integration/test_ws_server.py` and `test_ws_protocol.py` drive a real socket against a real `KFChessServer`. Integration tests in `tests/integration/scripts/` are executable `.kfc` text scripts that pin collision priorities and rule edge cases across refactors. Always run `pytest` to verify correctness after making changes:
  ```bash
  pytest                               # full suite
  pytest tests/unit/test_board.py      # one file
  pytest -k collision                  # by name
  ```

---

## License

This project is licensed under the [MIT License](LICENSE).
