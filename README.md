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

The codebase is built on **Clean Architecture** and follows **SOLID** design principles. The repository is split into three top-level packages with dependencies pointing one way — `client → shared ← server` — so the core game engine never imports UI or network code:

* **`shared/`** — the core game engine and domain models: `Event`/`EventBus` pub-sub, the `model`/`config` layer (`Position`, `ArrayBoard`, `TextPiece`, `GameState`, `Movement`, `Cooldown`, `Result`, `GameConfig`), pure `rules` (piece validators, `PathChecker`, `ThreatValidator`, `EndgameValidator`, `CastlingValidator`), the `realtime` tick loop (`RealTimeArbiter`, `CollisionResolver`, `ArrivalResolver`, `ProxyBoard`), the `engine` command dispatcher (`GameEngine`), the `input`/`view` DTO and pixel-mapping layer, and `io` (board parsing/printing, moves log, JSON history store, replay). No UI or network dependency lives here.
* **`client/`** — the Tkinter/Pillow multiplayer GUI. `client/auth/cli_auth.py` runs a terminal login/register handshake against the server before any window opens; `client/network/network_client.py` then owns a background-thread WebSocket connection for the game session itself; `client/main_gui.py` wires both into `client/ui/window/game_window.py`, which renders the board and turns clicks into algebraic-notation move requests (`client/notation/algebraic_notation.py`, `client/network/network_snapshot_decoder.py`).
* **`server/`** — the asyncio WebSocket server: connection routing and move relay are live today; authentication (`auth_service.py`, `database.py`), matchmaking/ELO (`matchmaker.py`, `elo.py`), and reconnection (`heartbeat.py`, `disconnect_handler.py`) exist as standalone, unit-tested modules that are not yet wired into the connection handler (see [Multiplayer Server](#multiplayer-server)). Imports `shared` only, never `client`.

Two boundaries hold the whole system together:

* **`shared/service.py` — `GameService`** is the *only* public entry point into the engine. The Tk window, the text-script runner, bots, and the server's game rooms all talk exclusively to it (`init_game`, `execute_command`, `click`, `right_click`, `advance_clock`, history save/load, `get_snapshot`, `get_moves`, event `subscribe`/`unsubscribe`) — nothing reaches through to `GameEngine`, the repositories, the arbiter, or the event bus directly.
* **`shared/bootstrap.py`** is the composition root. `build_service()` wires an instant-movement engine for tests and scripts; `build_realtime_service()` wires the real-time (`ChebyshevDistanceDuration`) engine plus moves log and history store used by the GUI and server.

This strict separation means the storage, interface, or network layer can be swapped without touching the rules or core simulation logic.

---
## Multiplayer Server

`server/` is an asyncio-based WebSocket server (built on `websockets`) that turns the local simulation into a networked multiplayer game. It's being built in phases, and what's actually wired into the live connection handler (`ws_server.py`, `game_room.py`) today is deliberately smaller than the set of modules that exist in the package:

* **Live today — connections, rooms & move relay** (`ws_server.py`, `game_room.py`, `protocol.py`): every new connection is auto-assigned White, then Black, into a single shared `"main_room"`; once both slots are filled the room builds a real `GameService` + `AsyncGameRunner` (real-time, `ChebyshevDistanceDuration`) and starts ticking. Further connections join as spectators. `move` messages are parsed via `AlgebraicParser` (`"e2"` ↔ `Position(6, 4)`), checked against the sender's assigned color, and applied directly through `GameEngine.request_move`; every tick and every accepted move broadcasts a fresh serialized `GameSnapshot` (`protocol.SnapshotSerializer`) to both players and any spectators. `ping` is answered with `pong`; any other message type gets an `error` reply — the handler does not yet branch on `auth` or `play`.
* **Built, not yet wired — authentication** (`auth_service.py`, `database.py`): registration/login backed by SQLite (`aiosqlite`), `bcrypt` password hashing, and a single persistent WAL-mode connection for concurrent safety. Each module has its own unit tests, but `ws_server.py`'s connection handler doesn't call it — the game connection is unauthenticated. `client/auth/cli_auth.py` performs this handshake over its own short-lived connection before the GUI opens, independently of the connection the game itself is played over.
* **Built, not yet wired — matchmaking & rating** (`matchmaker.py`, `elo.py`): a queue that pairs opponents within ±100 ELO (60-second timeout), and the standard ELO update formula. Not yet consulted when a connection is assigned to `"main_room"`.
* **Built, not yet wired — reconnection** (`heartbeat.py`, `disconnect_handler.py`): periodic ping/pong liveness checks and a 30-second reconnection window that would resync a returning player with a full snapshot. Not yet hooked into `ws_server.py`'s disconnect path.
* **Bots** (`player_interface.py`): a polymorphic player abstraction so a room can host an automated bot player transparently alongside human WebSocket players.

In short: today's server supports exactly one concurrent game (`"main_room"`), open to anyone who connects, with no accounts or ratings enforced on the play path. The module docstrings in `ws_server.py`/`game_room.py` label auth and matchmaking as later phases — treat the corresponding modules as ready components awaiting integration, not as live server behavior.

---
## Technological Stack & Current Status

* **Language**: Python 3 (CI runs on 3.10).
* **Multiplayer client**: `client/main_gui.py` is a networked client — it authenticates over the terminal (`client/auth/cli_auth.py`), then opens a Tkinter window that plays a real-time game over WebSocket against a running `main_server.py`. It no longer runs a self-contained local game; the local, server-less path (`shared/service.py`'s `GameService`) still backs `main.py`, the `.kfc` text-script runner, and bots.
* **Multiplayer server**: `main_server.py` runs the WebSocket server described above. Connection routing, a single shared game room, and move relay are live; account authentication, ELO-based matchmaking, and reconnection handling exist as separate, tested modules not yet wired into the connection flow (see [Multiplayer Server](#multiplayer-server)).

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

```bash
python main_server.py [--host HOST] [--port PORT] [--log-level {DEBUG,INFO,WARNING,ERROR}]
```

Defaults to `localhost:8765`. See [Multiplayer Server](#multiplayer-server) for what's actually live versus built-but-unwired; `client/main_gui.py` connects to it automatically once started.

---
## Tests

The project puts a heavy emphasis on reliability and mathematical accuracy, given the edge cases of real-time movement (e.g., division by zero guards, precise collision resolutions).
* **Framework**: `pytest`, plus `pytest-asyncio` for the WebSocket server's async tests.
* **Coverage**: 320+ unit and integration tests across the engine, UI, and server (matchmaking, auth, ELO, rooms, disconnect handling, protocol) — note these server modules are tested in isolation and, per [Multiplayer Server](#multiplayer-server), several aren't yet exercised through the live `ws_server.py` connection path. Integration tests in `tests/integration/scripts/` are executable `.kfc` text scripts that pin collision priorities and rule edge cases across refactors. Always run `pytest` to verify correctness after making changes:
  ```bash
  pytest                               # full suite
  pytest tests/unit/test_board.py      # one file
  pytest -k collision                  # by name
  ```

---

## License

This project is licensed under the [MIT License](LICENSE).
