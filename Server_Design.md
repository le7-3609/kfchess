# Server Design — Scaling Kung Fu Chess from One Process to a Fleet

This document is a work plan. It describes the server that exists in this repository today, the specific properties of that server that prevent it from scaling, and the ordered sequence of changes that turns it into a horizontally scalable system. Every section names the module it changes. Nothing here is a survey of available technologies; each step states what is built, against which file, and how you know it is finished.

The steps are ordered by dependency, not by importance. Each one leaves the system deployable and the test suite green, and each one is useful on its own even if the next step is never taken.

---

## 1. The system as it exists today

### 1.1 One process, one event loop

`main_server.py` is the entire server. It opens a single `Database` (`server/infrastructure/database/database.py`) against a SQLite file, constructs one `KFChessServer` (`server/presentation/ws_server.py`) listening for WebSocket connections on port 8765, and one `HttpApiServer` (`server/presentation/http_api.py`) serving three read-only REST routes on port 8080. Both run as coroutines on the same asyncio event loop, sharing the same `aiosqlite` connection.

This means every unit of work in the system — accepting a socket, hashing a password, validating a move, ticking a game simulation, serializing a board, writing a finished game to disk — is scheduled on one event loop, in one OS process, on one machine.

### 1.2 The connection path

A client connects and `KFChessServer._handle_connection` runs a fixed sequence. First `_authenticate` demands an `auth` frame and allows three attempts (`MAX_AUTH_ATTEMPTS = 3`) before closing the socket. Credentials go to `AuthUseCase` → `AuthService` (`server/application/auth_service.py`) → `Database.authenticate_user`, which verifies a bcrypt hash. Then `_establish_session` reads a second frame: `reconnect` rebinds an existing seat, anything else is dispatched through `_message_handlers`, the frame-type table that routes `play`, `create_room`, `join_room`, `move`, `ping` and `cancel_search` to the corresponding use case. The socket then sits in `async for raw_msg in websocket`, dispatching through the same table until it closes.

The session object is `server/presentation/ws_connection.py::PlayerSession`, which holds the live WebSocket handle and does the JSON encoding. The server keeps every one of them in `KFChessServer._sessions`, a plain dict keyed by the websocket object.

### 1.3 The room

`MatchmakingUseCase` pairs players and asks `RoomManager` (`server/application/room_manager.py`) for a room. `RoomManager.create_room` generates a six-character alphanumeric id, builds a `server/application/game_room.py::GameRoom`, and indexes it in `RoomManager._rooms`.

`GameRoom` is the seam between the network and the simulation. When the second seat fills, `_wire_infrastructure_after_init` builds the `core` engine stack, subscribes a `NetworkBroadcastObserver` to the engine's `EventBus`, and starts an `AsyncGameRunner` (`core/runtime/async_runner.py`) at `DEFAULT_TICK_RATE_HZ = 20.0`. From then on, twenty times a second, the runner advances the clock by measured wall-clock time, drains queued commands into `GameEngine`, and calls `GameRoom._on_tick` → `_broadcast_state`.

The game rules themselves are untouched by any of this. `core/` is a pure simulation library with no network dependency, consumed through `GameService`/`bootstrap`, and it stays that way through every step below.

### 1.4 Everything that matters lives in RAM

Five pieces of state exist only inside the process and disappear when it stops:

| State | Lives in | Consequence |
|---|---|---|
| Connected sessions | `KFChessServer._sessions` | A second process cannot see or reach a connected player |
| Active rooms | `RoomManager._rooms`, `RoomManager._session_rooms` | A room is reachable only from the process that created it |
| The live simulation | `GameRoom._core`, `_runner` | A crash loses the game outright |
| Matchmaking queue | `MatchmakingQueue._queue` (a list guarded by an `asyncio.Lock`) | Two players on two processes can never be matched |
| Disconnect countdowns | `DisconnectHandler._countdown_tasks` | A restart forfeits every disconnected player at once |

### 1.5 Persistence

`Database` opens one `aiosqlite` connection at startup, enables WAL and foreign keys, and runs `CREATE TABLE IF NOT EXISTS` for `users`, `games`, `moves`, and `game_statistics`. Every client-reachable statement goes through `SecureQueryExecutor` (`server/infrastructure/database/query_executor.py`), which rejects stacked statements, enforces placeholder arity, refuses unbindable values, and converts driver failures into a generic `Result.fail` so no SQLite error text ever reaches a client. `save_completed_game` writes the game row, its moves, and both players' recomputed statistics inside a single transaction that rolls back as a unit.

### 1.6 What was not there yet

These were the gaps this document was written against. Steps 1–4 below have since closed them, and Section 3 records what landed; the description is kept because the reasoning in those steps only makes sense against the server they were reacting to.

There was no transport security: `websockets.serve` was called without an `ssl` argument and `web.TCPSite` without an `ssl_context`, so credentials crossed the network in plaintext. There was no rate limiting of any kind — the three-attempt auth budget is per connection, and a client could open connections without limit. There was no token: every new socket re-sent the password. `HeartbeatMonitor` (`server/infrastructure/services/heartbeat.py`) was written and unit-tested but never instantiated by `ws_server.py`, and no handler routed an inbound `pong`, so a half-open socket was only noticed when a write failed. Observability was `logging.basicConfig` writing unstructured lines to stdout. CI (`.github/workflows/tests.yml`) ran pytest on Python 3.10 and produced no artifact.

---

## 2. The target, and the ceiling the current code actually has

The target is 100,000,000 registered users, 10,000,000 concurrent players, and millions of moves per second. A single machine has a fixed ceiling on memory, CPU, network bandwidth and availability, so the goal is not a faster server but a fleet of cooperating servers that behaves, from the client's side, like one enormous always-available one.

Before adding machines it is worth knowing what one machine currently does. The relevant number is measurable from the code rather than assumed. Serializing the opening position through `SnapshotSerializer.serialize` and wrapping it with `build_game_state_message` produces a **5,480-byte** JSON frame for 32 pieces. `GameRoom._on_tick` sends that frame to every seated player and every viewer, twenty times per second, whether or not anything changed. A two-player room with no spectators therefore emits:

```
2 recipients x 20 frames/s x 5,480 B = 219 KB/s = ~1.75 Mbps per room
```

On a 1 Gbps interface that is roughly **570 concurrent rooms, or about 1,100 concurrent players per container** — bandwidth-bound, long before CPU or memory become interesting. Any capacity plan built on "20,000 players per container" is arithmetic about a server that does not exist yet.

The same room also emits sparse events through `NetworkBroadcastObserver`, which are already the right shape: a serialized `MoveStartedEvent` measures **123 bytes** and carries `from`, `to`, `arrival_ms` and `at_ms` — everything a client needs to interpolate the piece's travel locally. At roughly one move per player per two seconds, event-only traffic for the same room is about 500 B/s, some **440x** less than the snapshot stream that currently runs alongside it.

That ratio is why Step 2 comes before any infrastructure work. Sharding a protocol this expensive means buying 440 times more hardware than the game needs.

---

## 3. The order of work

| # | Step | Primary files | Done when | Status |
|---|---|---|---|---|
| 1 | Deployable, observable baseline | `Dockerfile`, `docker-compose.yml`, `http_api.py`, `ws_server.py`, CI workflow | CI publishes a tagged image; `/healthz` and `/readyz` answer; metrics are scraped | **Built** |
| 2 | Fix the wire protocol | `game_room.py`, `broadcast_observer.py`, `protocol_mapper.py` | Steady-state bandwidth per room is measured in hundreds of B/s, not hundreds of KB/s | **Built** — measured below |
| 3 | Security at the edge | `main_server.py`, `auth_service.py`, `ws_server.py`, `database.py` | TLS terminated, tokens issued, rate limits enforced, writes idempotent | **Built** |
| 4 | Split the process into roles | `main_server.py` → two entry points | HTTP and WebSocket deploy, restart and scale independently | **Built** |
| 5 | Move state out of the process | `room_manager.py`, `queue.py`, `database.py` | Two server replicas serve one logical game population | **Built** — verified below |
| 6 | Introduce the broker | `broadcast_observer.py`, `ws_server.py` | A player on replica A sees moves computed on replica B | **Built** — verified below |
| 7 | Shard the game authority | `domain/coordination/leases.py`, `room_ownership.py` | Rooms are owned by lease; killing an owner loses only its rooms | **Partly built** — see below |
| 8 | Kubernetes, autoscaling, persistence pipeline | `deploy/`, `persistence_worker.py` | Replica counts track load; a database stall does not stall a game | **Partly built** — see below |

Steps 1 through 4 change this repository and need no new infrastructure. Steps 5 through 8 add infrastructure (Redis, NATS, PostgreSQL, Kubernetes), each piece introduced only once the code had a place to put it.

Every backing store is reached through a port with two implementations — one in-process, one against the real service — so `pytest` runs green with no containers at all, and `pytest tests/infra` runs the same expectations against Redis, PostgreSQL and NATS. That is not a convenience: an in-memory dict agrees with any contract, including a wrong one, so the second implementation is what actually tests the first.

### What Steps 1–4 actually landed

- **Probes and drain.** `/healthz` (liveness, touches nothing), `/readyz` (readiness, pings the database), `/metrics`. `ReadinessProbe.begin_draining` reports not-ready while staying alive, and `app_runner` installs the SIGTERM handler that triggers it — the mechanism Step 7's `preStop` drain builds on.
- **Metrics.** A dependency-free Prometheus registry (`server/infrastructure/observability/`) exporting room count, session count, queue length, active disconnect countdowns, broadcast failures, frame/auth/rate-limit counters, the API tier's RED metrics, and the **tick-duration histogram** fed from `AsyncGameRunner` through an optional callback, so `core` keeps no metrics dependency.
- **Structured logging.** JSON records with `room_id`/`user_id` carried in context variables, which are per-task and therefore cannot bleed between two rooms ticking on one loop.
- **Heartbeat.** `HeartbeatMonitor` is constructed, started, registered per session and fed by a `pong` handler; an unanswered ping closes the socket, which frees the seat through the existing teardown path.
- **Protocol.** The per-tick broadcast is gone. The snapshot is split per recipient so one player's selection never reaches the other, and `move_id` makes a repeated move frame a no-op.
- **Security.** Optional TLS on both listeners, `X-Forwarded-For` believed only from configured proxies, bcrypt pinned at cost 12 and moved off the event loop with rehash-on-login, HS256 session tokens issued by the API tier and verified by the socket tier, four rate limits (per-IP connections, per-session frames, per-user rooms, HTTP per caller), and `ON CONFLICT (room_id) DO NOTHING` making a finished-game save idempotent.
- **Roles.** `main_ws.py` and `main_api.py` from one image, `main_server.py` retained for local development.

### Step 2's exit criterion, measured

Measured over a real socket, two players at one move per player per two seconds:

| Protocol | Per room | vs. before |
|---|---|---|
| Snapshot every tick (before) | 219,200 B/s | — |
| Event stream alone | **206 B/s** | 1064x less |
| Event stream + reconciliation every 5 s | 2,394 B/s | 92x less |

The steady-state protocol is in the hundreds of bytes per second as the criterion requires. The reconciliation frame is what adds the rest, and its interval is the knob that trades bandwidth against how long a client can stay drifted. Five seconds is sized for the transport events run on *next*: today they share one ordered TCP socket and barely drift, but Step 6 moves them to a fire-and-forget broker where a dropped event must be repaired in seconds.

### What Steps 5–8 landed, and what they did not

**Step 5 — state out of the process.** `MatchmakingQueue` keeps its interface (`join_queue`, `leave_queue`, `try_match`, `check_timeouts`) and gains a store behind it: a Redis sorted set by rating, so the ±100 window is a `ZRANGEBYSCORE` rather than the O(n²) pair scan, plus a second set by join time so pairing still favours the longest wait. The pop is one Lua script that takes both players or neither. Session and room directories are Redis keys with a 10-minute TTL, so `find_room_by_username` no longer scans local rooms. `Database` is joined by `PostgresDatabase` over `asyncpg` — `?` → `$n`, `INSERT OR IGNORE` → `ON CONFLICT`, one connection → a pool — and `SecureQueryExecutor`'s contract is shared verbatim between the two drivers via `query_contract.py`, with all 24 of its tests ported and passing against PostgreSQL. Schema management is Alembic, applied by a job that runs to completion before any replica starts.

*Verified:* `tests/infra/test_cross_replica_play.py` runs two `KFChessServer` instances on two ports against one Redis and one NATS, with real WebSocket clients. A player who queues on replica A is matched against one who queues on replica B, and the seat is resolvable from either replica's directory.

**Step 6 — the broker.** `NetworkBroadcastObserver._broadcast` publishes to `room.<room_id>.events` in addition to writing the sockets it holds locally; `_EVENT_SERIALIZERS`, the event types and `core/events.py` are untouched. `GatewayRelay` subscribes on behalf of each connected client — one subscription per room shared by every local member, reference-counted so a spectator leaving does not cut off the players. Room events run on NATS core and `game.finished` on JetStream, chosen from failure tolerance: a dropped event costs one reconciliation interval, a dropped result is a game nobody ever wrote. `/readyz` gained the broker check.

*Verified:* a move made by the player on one replica is rendered by the opponent on the other, carrying the `arrival_ms` window the client interpolates over.

**Step 7 — leases. Partly built.** The mechanism is complete and tested: `SET NX PX 30000` with renewal at 10s, a fencing token in its own non-expiring key so a room going briefly unowned cannot reset the generation, renewal and release both verifying owner *and* token, rendezvous-hash placement, and a drain that stops taking rooms while continuing to renew what it holds. The failure injection the exit criterion demands is `test_killing_an_owner_bounds_the_damage_to_its_own_rooms`: two instances hold three rooms each, one stops renewing without releasing, and what is asserted is that its ids become acquirable while the survivor's are untouched.

*What is not built:* `RoomOwnership` is not yet driving the gateway's room lifecycle — a room is still owned implicitly by the instance that created it. The single-authority property is nonetheless enforced structurally today, because only that instance subscribes to `room.<id>.commands`, which is where the design says the enforcement belongs. What is missing is lease-based *placement* and *failover*: today a crashed instance's rooms are lost without another instance acquiring the ids. The gateway and game authority also remain one process rather than two.

**Step 8 — orchestration. Partly built.** `GameRoom` no longer awaits the database on game end: it publishes to the durable stream and frees the room, falling back to an inline write only when no broker exists or the publish fails. `PersistenceWorker` consumes the stream as a named consumer group and writes in batches bounded by both size and age. Kubernetes manifests exist for every role and were applied to a real k3d cluster (k3s v1.35.5); all six application pods reached Ready, the Alembic job ran to completion first, and Prometheus scraped every role's `/metrics`.

*What is not built:* the HPAs are written against the exported gauges but nothing converts those into the custom-metrics API — that needs `prometheus-adapter` or KEDA, neither of which is deployed, so the gateway and worker HPAs report `<unknown>` rather than scaling. The autoscaling exit criterion is therefore **not** demonstrated. The data stores are also not split by shape (Section 11.4): game history is still in PostgreSQL rather than a wide-column store.

---

## 4. Step 1 — A deployable, observable baseline

Nothing about the architecture changes here. The point is that every later step is verified by deploying it, and today there is no deployment to compare against.

**Containerize the process as it is.** `Dockerfile` builds from `python:3.10-slim`, installs `requirements.txt` before copying source so editing code does not invalidate the pip layer, copies `core/`, `server/` and `main_server.py`, creates a non-root user, and leaves the SQLite file on `/data` because a database written into an image layer is discarded with the container. The command lives in `docker-compose.yml` rather than as a baked-in `CMD`, because Step 4 splits this image into two roles that differ only in their entry command.

`docker-compose.yml` runs that image against a named volume and declares PostgreSQL and NATS under a `future` profile — started with `--profile future`, wired to nothing yet. They are present so that Steps 5 and 6 are a code change against a running dependency rather than a code change and an environment change at the same time. Compose is not a production topology and never becomes one; it exists so the whole system can be brought up on one machine, and so a CI job can start the stack, run `tests/integration/test_ws_server.py` against it, and tear it down.

**Give the process real health endpoints.** The compose healthcheck currently calls `/api/leaderboard`, which reads the database. That is precisely the wrong shape for a probe and must not be carried into Kubernetes: a liveness check that touches the database turns one slow database into a fleet-wide restart storm, because every replica fails simultaneously and the restarts hit the struggling database harder. Add two routes to `HttpApi.build_app`:

- `/healthz` — liveness. Purely local: the process is running and its event loop is responsive. It touches nothing external.
- `/readyz` — readiness. "I can do useful work right now": the database connection is open, and (after Step 6) the broker connection is established. A replica that is draining reports **not ready but still alive**, so it stops receiving new work without having its live rooms killed.

**Wire the heartbeat that already exists.** `HeartbeatMonitor` is complete and tested but never constructed. Instantiate it in `KFChessServer.__init__`, register each session in `_establish_session`, unregister it in the `finally` block of `_handle_connection`, and add a `pong` row to `_message_handlers` calling `record_pong`. Without this, a client whose network vanishes without a TCP FIN is detected only when `PlayerSession.send` raises — and `PlayerSession.send` returns silently when `connected` is false, so it may never be detected at all. The seat then sits occupied until the game ends on its own.

**Replace print-shaped logging with structured logging.** `main_server.py` configures a human-readable formatter. Replace it with a JSON formatter and attach `room_id` and `user_id` to every record emitted below the connection handler. A container's local filesystem dies with the container, so logs are shipped to a central store; correlating a single game across the processes that handled it is only possible if the identifiers are fields rather than prose.

**Export metrics.** Add a Prometheus endpoint on the HTTP port and export gauges read directly off objects that already exist: `RoomManager.room_count`, `MatchmakingQueue.queue_length`, `len(KFChessServer._sessions)`, and the size of `DisconnectHandler._countdown_tasks`. Add one histogram that matters more than the rest: **tick duration**, measured inside `AsyncGameRunner._tick`. The runner is supposed to complete a tick within its 50 ms budget; when it does not, the simulation is falling behind wall-clock time and every player in every room on that process feels it. No CPU percentage reveals that. Also count the broadcast failures currently swallowed by the `except` in `GameRoom._broadcast_state` — today a room can be failing to reach a client on every single tick and emit nothing but a debug-level warning.

**Extend CI.** The existing workflow runs pytest on 3.10. Add a job that builds the image and pushes it tagged with the commit SHA, and a job that brings up Compose and runs the integration suite against the real socket. From here on, "deploy" means pointing at a new image tag.

**Exit criterion:** `docker compose up` yields a working server; CI publishes an image per merge; `/healthz` and `/readyz` behave differently under a database outage; a Prometheus scrape returns room count, connection count and tick-duration percentiles.

---

## 5. Step 2 — Fix the wire protocol

This is a change to `server/application/`, with no infrastructure dependency, and it is worth more than any single infrastructure step. It is the difference between roughly 1,100 and roughly 20,000 players per container.

**Stop broadcasting the full snapshot on every tick.** `GameRoom._on_tick` currently calls `_broadcast_state` unconditionally, twenty times a second. Because pieces travel over time rather than teleporting, the client does not need frame-by-frame position updates; it needs to know when a move *starts* and how long it will take, and can then interpolate the travel itself. That information is already published: `MoveStartedEvent` carries `frm`, `to`, `arrival_ms` and `at_ms`, and `NetworkBroadcastObserver` already serializes it.

Remove the `on_tick` broadcast. The event stream becomes the only steady-state traffic, and it is emitted only when something actually happens.

**Keep exactly three cases that still need a full snapshot.** Game start, reconnection (`DisconnectHandler.handle_reconnect` already does this, and correctly — it pushes a complete `GameSnapshot` as one atomic payload so the returning client overwrites rather than patches its state), and a low-frequency reconciliation frame — once every few seconds — that repairs any drift from a dropped event. That last one is what makes it safe to run the event channel on a fire-and-forget transport in Step 6: a lost event is corrected within seconds instead of desynchronizing the game permanently.

**Make the snapshot per-recipient.** `_broadcast_state` builds one serialized snapshot and sends the identical bytes to both players and every viewer. That snapshot contains `selected_pos`, `legal_move_targets` and `castle_targets` — one player's current selection and the squares highlighted for them. Sending it to the opponent leaks intent and wastes bytes. Split the serialization: the shared board state is computed once per room, and the selection-derived fields are attached per recipient.

**Add a client-supplied `move_id` to the move frame.** `_handle_move` today has no notion of a repeated request, and `AsyncGameRunner.submit_command` queues into an unbounded `asyncio.Queue`. A client that retries a move after a flaky reconnect submits it twice, and both are executed. Require a client-generated unique id on the `move` frame, hold a bounded set of recently-seen ids per room, and answer a repeat with the original `Result` instead of re-executing. This is the first of three idempotency measures; the other two are in Step 3.

**Exit criterion:** a two-player room's measured steady-state outbound traffic is in the hundreds of bytes per second; a reconnecting client still recovers full state in one frame; the text-script integration tests in `tests/integration/scripts/` still pass, since none of this touches `core/`.

---

## 6. Step 3 — Security at the edge

Everything here is required before the server is reachable from an untrusted network, and all of it is cheaper to build while the system is still one process.

### 6.1 TLS

Browsers refuse a plaintext `ws://` connection from an `https://` page, and today both listeners are plaintext. The decision is to **terminate TLS at the ingress, not in the application**: the ingress (an NGINX or Envoy ingress controller in Step 8, and `wss://play.kfchess.com` from the client's perspective) holds the certificate, and traffic inside the cluster is plaintext on a private network. This keeps certificate rotation out of the application's lifecycle and off the game event loop.

Two consequences must be handled in code rather than assumed. The application must read the client address from `X-Forwarded-For` and must trust that header **only** when the connection arrives from the ingress — otherwise the per-IP rate limits below are trivially bypassed by forging it. And the ingress must be configured for WebSocket upgrade with an idle timeout longer than a game, or long-lived sockets are cut mid-play.

For local development and the Compose stack, `websockets.serve` accepts an `ssl` argument and `web.TCPSite` accepts `ssl_context`; both are plumbed through as optional CLI flags so a developer can reproduce a TLS problem without an ingress.

### 6.2 Password hashing

The current scheme is correct and stays: `Database.create_user` hashes with `bcrypt.hashpw(password, bcrypt.gensalt())` and `authenticate_user` verifies with `bcrypt.checkpw`. Passwords are never logged — `SecureQueryExecutor._contain` deliberately omits bound parameters from its log line for exactly this reason — and `AuthService.login` returns one identical message for every failure so an unauthenticated caller cannot distinguish "no such user" from "wrong password".

Three things change.

**Pin the cost factor explicitly.** `bcrypt.gensalt()` uses the library default, which has moved between releases. Pin it in one named constant so the work factor is a deliberate, reviewable number rather than a property of whichever bcrypt version the image resolved.

**Get bcrypt off the event loop.** `bcrypt.checkpw` is synchronous CPU work, and it is called from `Database.authenticate_user` on the same event loop that runs every room's `AsyncGameRunner`. A single login at cost 12 blocks that loop for on the order of a quarter of a second, which is five missed ticks in every game on that process. Wrap both `hashpw` and `checkpw` in `asyncio.to_thread`. This is a correctness fix for the game, not only a throughput fix for auth.

**Support rehashing on login.** Store the cost with the hash (bcrypt already does) and, when a successful login presents a hash below the current target cost, rehash and update it in place. Otherwise the cost factor can never be raised for existing accounts.

### 6.3 Tokens: authenticate once, not once per socket

Today every WebSocket connection carries a username and password and performs a bcrypt verification. That makes bcrypt's deliberate slowness an availability problem, and it means the password crosses the wire on every reconnect — including the reconnect that follows every deploy of the WebSocket tier.

Move issuance to the HTTP side. `POST /api/auth/login` on `HttpApi` verifies credentials once and returns a short-lived signed token (a JWT with `user_id`, `username` and an expiry of roughly one hour) plus a longer-lived refresh token. The `auth` frame in `ws_server._authenticate` then accepts a token and *verifies a signature* instead of consulting the user database, which is both far cheaper and the property Step 4 depends on: once the WebSocket tier validates an already-issued token, it no longer needs the user table at all.

The signing key is supplied as an environment variable (a Kubernetes `Secret` later), never committed, and rotated with an overlap window during which both the previous and current key verify.

The identity in the token is authoritative. `GameSessionUseCase.reconnect` already gets this right and the rule generalizes: the seat to rebind is always the authenticated identity from this connection's own handshake, and a `username` carried in the frame is accepted only as a same-user sanity check, never as the thing that selects the seat.

### 6.4 Rate limiting

There is none today, and there are four distinct paths that need it. Counters live in Redis from Step 5 onward so a limit is per user rather than per replica; until then they are in-process, which is still better than nothing.

**Connection establishment, per source IP.** The auth budget of three attempts in `_authenticate` is per connection, and connections are unlimited. Since each attempt costs a bcrypt verification, an attacker can saturate the CPU of the whole process from one host. Limit new connections per IP per minute at the ingress, and add a per-username exponential backoff after consecutive failures, keyed in Redis so it survives a reconnect to a different replica.

**Frames per socket.** `_process_message` dispatches every frame that arrives with no ceiling, and `move` frames flow into an unbounded queue. Apply a token bucket per session before `_dispatch_message`: a generous steady rate with a small burst allowance, sized well above what real play produces. Over-limit frames are answered with an error frame and dropped, and a socket that stays over the limit is closed.

**Room creation.** `_handle_create_room` is unbounded per user, and `RoomManager._generate_unique_room_id` tries up to 100 times against an in-memory dict. Cap concurrent rooms per user and creations per minute.

**The HTTP API.** `/api/games/{game_id}` accepts any integer, so the entire game history can be walked by counting up. Rate limit per IP and per token, and cap `/api/leaderboard` result size — it already has `DEFAULT_LEADERBOARD_LIMIT = 100`, which should be enforced as a maximum rather than only a default.

### 6.5 Idempotency

Retries are not an edge case in this system: mobile networks drop, Step 8 delivers persistence events at-least-once, and a deploy of the WebSocket tier reconnects every client at once. Every write path must be safe to repeat.

**Finished games.** `games.room_id` is `UNIQUE`, and `save_completed_game` wraps the game row, its moves and both players' recomputed statistics in one transaction that rolls back as a unit — so a duplicate save cannot produce a half-written game. But it returns `None` for a duplicate exactly as it does for a genuine failure, and `GamePersistenceService` logs both as "was not persisted". Change the insert to `ON CONFLICT (room_id) DO NOTHING RETURNING id` and have the service treat "a row already exists for this room" as success. This matters immediately, because a natural game end and a disconnect forfeit can race the same room, and it matters more in Step 8 when a redelivered event reaches a worker that already wrote the row.

**Moves.** Covered by the `move_id` introduced in Step 2.

**Rating updates.** `Database.update_elo` writes an absolute value that both `_settle_elo_for_game_end` and `_apply_forfeit` compute from the pre-game ratings. Replaying that write produces the same row, so it is already idempotent — which is the reason to keep it absolute and never rewrite it as an increment.

**Pairing.** `MatchmakingQueue.try_match` is idempotent by construction: it identifies a pair and removes both entries with zero `await` between the two operations, so no coroutine can observe or re-match a half-evicted pair. Step 5 moves this queue to Redis, and preserving that atomicity — one Lua script that pops both players or neither — is a hard requirement of that step, not an optimization.

**Exit criterion:** credentials never cross a plaintext connection; a login costs one bcrypt verification per session rather than one per socket; bcrypt no longer runs on the game loop; each of the four rate limits is exercised by a test; replaying a finished-game save, a move frame and an ELO update leaves the database identical.

---

## 7. Step 4 — Split the process into roles

`main_server.py` starts a WebSocket server and an HTTP server on one loop. They have almost nothing in common operationally:

| | HTTP API | WebSocket server |
|---|---|---|
| Connection lifetime | Milliseconds, one per request | Minutes to hours, one per session |
| What limits an instance | CPU and database round-trips | Memory and file descriptors |
| Scaling signal | Requests/sec, p99 latency | Open connection count |
| Effect of a restart | Nothing; clients retry | Every client on it must reconnect |

Running them together means a burst of replay queries — database-bound, CPU-heavy JSON serialization — competes for the event loop that is supposed to be forwarding moves, and it means deploying a change to the leaderboard disconnects every player mid-game.

Split `main_server.py` into two entry points from the same image: `main_api.py` constructing only `HttpApiServer`, and `main_ws.py` constructing only `KFChessServer`. The `Dockerfile` already anticipates this by keeping the command in the orchestration file rather than baking a `CMD`. Both remain stateless with respect to identity: the API tier issues tokens (Step 3) and the WebSocket tier validates them.

**Load balancing.** Clients connect to one stable address and a load balancer distributes new connections across the current pool. For WebSocket this is an L4 or WebSocket-aware L7 balancer capable of long-lived connections. No session affinity is required: placement matters only at connect time, since a socket stays pinned to whichever instance accepted it. Round-robin or least-connections, decided once per connection, is sufficient.

### 7.1 CDN

Splitting the API tier out is what makes a CDN useful, because the responses worth caching all live there.

**Replay and PGN responses are immutable.** A row in `games` is written once, inside one transaction, and never updated; `room_id` is `UNIQUE`. So `GET /api/games/{id}` and `GET /api/games/{id}/pgn` return content that can never change. Serve them with `Cache-Control: public, max-age=31536000, immutable` and an `ETag`. Every repeat view of a completed game is then answered by a CDN edge and never reaches PostgreSQL — which matters at the projected rate of finished games, where history reads dominate history writes by orders of magnitude.

**The leaderboard is cacheable but not immutable.** `GET /api/leaderboard` is a top-100 ordering that changes constantly and matters to nobody within a few seconds. `Cache-Control: public, max-age=30, stale-while-revalidate=60` collapses arbitrary read volume into two origin queries per minute.

**Authenticated and real-time paths are never cached.** Token issuance and the WebSocket upgrade carry `Cache-Control: no-store`.

**The client bundle.** `client/` is a Tkinter desktop application today, so there are no web assets to serve yet. When a browser client exists, its JavaScript, CSS and piece sprites are content-hashed and served from the CDN, which is also where TLS is terminated at an edge PoP — shortening the handshake round-trip for the initial connection regardless of where the game itself runs.

**Exit criterion:** the two roles build from one image and deploy independently; restarting the API tier drops no game socket; a repeated replay fetch is served from cache with no database query.

---

## 8. Step 5 — Move state out of the process

Two replicas of the WebSocket server today are two disjoint games: a player on replica A cannot be matched against, join, or spectate anything on replica B. Section 1.4 lists the five pieces of state responsible. This step externalizes them.

**Matchmaking queue → Redis.** `MatchmakingQueue` holds a Python list guarded by an `asyncio.Lock`, which coordinates coroutines within one process and nothing beyond it. Replace the backing store with a Redis sorted set keyed by rating; the ±100 ELO window becomes a `ZRANGEBYSCORE` around the candidate instead of the current O(n²) pair scan, which also removes a real CPU cost at queue depth. The pairing must remain atomic across replicas, so eviction moves into a Lua script that pops both players or neither — preserving exactly the property `try_match` documents today. `MatchmakingQueue`'s public interface (`join_queue`, `leave_queue`, `try_match`, `check_timeouts`) is unchanged, so `MatchmakingUseCase` and its tests are untouched.

**Session and room directory → Redis.** `KFChessServer._sessions` and `RoomManager._session_rooms` become Redis hashes: `user_id → {replica, room_id}` and `room_id → owner`. This is what makes reconnect work across replicas. Today `RoomManager.find_room_by_username` scans every local room looking for a disconnected seat; with the directory in Redis, a returning client that lands on any replica is routed to the right room immediately. The mapping carries a TTL matched to the expected game lifetime.

**SQLite → PostgreSQL.** SQLite is not the wrong choice because of row count — 100M rows is comfortable for any server-based RDBMS — but because of three structural limits. It allows one writer at a time even in WAL mode, which only improves concurrent reads. It has no replication and no sharding, so the machine holding the file is a single point of data loss. And it is embedded rather than client-server, so every process that reads or writes it needs filesystem access to the same file — impossible across hundreds of processes on many hosts.

The migration is contained because `Database` is already the only module that knows SQL, and `SecureQueryExecutor` is the only path client-supplied values take to reach it. Porting means changing the driver to `asyncpg`, converting `?` placeholders to `$n`, replacing `INSERT OR IGNORE`-style behavior with `ON CONFLICT`, and introducing a connection pool in place of the single connection. The executor's contract — no stacked statements, no unbindable values, allowlisted identifiers, generic error messages — carries over unchanged and must be ported with its tests.

Schema management changes too. `Database.connect` runs `CREATE TABLE IF NOT EXISTS` on every startup, which is fine for a file that one process owns and wrong when fifty replicas start simultaneously against a shared database. Migrations move to Alembic, applied by a pre-deploy job that runs to completion before any new replica starts.

**Exit criterion:** two replicas behind one load balancer serve a single player population — a player on one is matched against a player on the other, and reconnects onto either.

---

## 9. Step 6 — The broker

After Step 5 two replicas share a player population, but a move computed on replica B still has to reach a socket held by replica A. Direct addressing between replicas does not scale: every instance would need to know how to reach every other, in a fleet that is constantly being rescheduled.

Instead every instance talks only to a shared broker. Each room gets a channel — `room.<room_id>.events` — the game's owner publishes to it, and every player and spectator in that room is a subscriber through whichever gateway holds their socket. The publisher does not know, and does not need to know, where any subscriber is running.

This is the same publish/subscribe shape the client protocol already has, moved inward, and the code is already arranged for it. `NetworkBroadcastObserver` subscribes to the engine's `EventBus` and turns each domain event into a wire frame through the `_EVENT_SERIALIZERS` table. Today `_broadcast` writes those frames to sockets it holds directly. The change is that `_broadcast` publishes to the room channel instead, and the gateway subscribes on behalf of each connected client. The serialization table, the event types, and `core/events.py` are untouched.

Two kinds of traffic flow through the broker and they have genuinely different requirements, so they do not share a technology:

| Traffic | Requirement | Choice |
|---|---|---|
| `room.*.events` | Lowest possible latency; occasional loss is acceptable, because Step 2's periodic reconciliation frame repairs drift within seconds | NATS core (or Redis Pub/Sub) — fire-and-forget, no persistence |
| `game.finished` | Must not silently drop; a lost message is a game that is never saved | NATS JetStream — durable, replayable, consumer groups that resume after a crash |

Both are provided by the NATS service already declared under the `future` profile in `docker-compose.yml`, started with `-js` to enable JetStream.

`/readyz` now depends on the broker connection. A gateway that reports ready before it can reach the broker will be handed live connections it cannot serve.

**Exit criterion:** with two replicas running, a move made by a player connected to one is rendered by an opponent connected to the other, within the latency budget.

---

## 10. Step 7 — Sharded game authority with ownership leases

With the broker in place, holding a connection and computing a game are separable jobs, and they should be separated. Holding an open socket is work in itself: it costs memory and a file descriptor for the whole session whether or not moves are happening, and a process doing both competes with itself. Split the WebSocket role into two:

- **Gateway** (stateless) — terminates the WebSocket, validates the token, forwards inbound frames to the room's channel and outbound events back down the socket. It computes no game logic and can be deployed close to players geographically.
- **Game authority** (stateful) — runs `GameRoom` and the `core` engine for a subset of active rooms. This is the existing `RoomManager` + `GameRoom` code, with its transport replaced by broker publish/subscribe.

**Ownership must be single and exclusive.** If two instances computed the same room, nothing guarantees they would agree — one could rule a move legal and the other illegal, and the game would have two divergent histories with no way to reconcile them. So every room has exactly one owner, only the owner may publish state changes for it, and every other instance can forward requests to it but never act on the room itself.

**Ownership is a lease, not an assignment.** Assignment cannot be a fixed range like "instance A owns rooms 1–100,000": at the churn rate derived in Section 11, ranges desynchronize within seconds. It is dynamic — a new room goes to the least-loaded registered instance, or is placed by rendezvous hashing over the room id and the current live instance set, which minimizes reshuffling when instances come and go.

The mapping lives in Redis as a lease with a TTL. An instance takes a room with `SET room:<id>:owner <instance> NX PX 30000` — set only if absent, expiring in 30 seconds — and renews every 10 seconds while the room is active. If the owner crashes it stops renewing, the key expires, and another instance acquires it.

**A crashed owner loses its in-flight games, and that is the accepted behavior.** The simulation lives in `GameRoom._core` in memory. Checkpointing piece positions, clocks and cooldowns to Redis on every move would allow a new owner to reconstruct the position, but a game here lasts 30 to 90 seconds; the decision is to reconnect affected players into a fresh game rather than carry the complexity of state reconstruction for a sub-minute session. What the lease buys is not game continuity but **blast radius**: a crash loses the rooms one instance owned, not the rooms of the fleet.

**Guard against false failover.** A network hiccup, not a crash, can also make an owner miss a renewal. Each acquisition carries a monotonically increasing fencing token, and a stale token is rejected downstream. In practice this is enforced structurally: the owner is the sole publisher on its room's channel, and broker subscription follows the lease, so a resurrected old owner physically cannot publish to a channel it no longer holds.

**Deploys must drain, not kill.** An instance holding thousands of live rooms cannot be terminated abruptly. On `SIGTERM` it deregisters from the scheduler so it receives no new rooms, reports not-ready but still alive, and lets existing rooms run to completion. Because games last 30 to 90 seconds, a `terminationGracePeriodSeconds` of two to three minutes with a `preStop` hook empties an instance without dropping a game in progress.

**Cross-region placement.** Matchmaking prefers same-region pairing. If no partner appears within a bounded wait, it falls back to cross-region and places the room in whichever candidate region minimizes the **sum** of both players' round-trip times, rather than always favoring one side.

**Exit criterion:** killing a game-authority instance holding live rooms causes its leases to expire, another instance to acquire the room ids, and the failure to be bounded to the rooms it owned — verified as a deliberate failure-injection test, not an inference.

---

## 11. Step 8 — Kubernetes, autoscaling, and the persistence pipeline

### 11.1 Why the fleet needs an orchestrator

Docker gives identical, interchangeable units. Something still has to decide how many of each exist right now, place them, and replace the ones that die. That is Kubernetes (or K3s, the same model as a single binary, appropriate for a smaller cluster).

Kubernetes runs no game logic and receives no move. Adding a gateway replica is four steps: start a Pod from the same image; wait for its readiness probe to pass; add it to the Service's endpoint list; from that moment it receives a share of new connections, invisibly to clients already connected elsewhere. Removing one, and replacing a crashed one after a failed liveness probe, are the same mechanism in reverse. The probes are the ones defined in Step 1, and their semantics are the contract this entire mechanism rests on.

### 11.2 Autoscaling signals differ sharply by role

CPU is the default HPA metric and it is the wrong one for most roles here.

For the **gateway**, an idle keep-alive socket costs almost no CPU but still occupies memory and a file descriptor. Scale on open connection count, exported to Prometheus and read through the Prometheus adapter or KEDA. Connection count also stays elevated far longer than any single game, because one player plays many games in a row on the same socket.

For the **game authority**, scale on active room count, and scale *fast*. Little's Law gives the churn rate this fleet must absorb:

```
10,000,000 players / 2 per game        = 5,000,000 concurrent games
5,000,000 games / ~60 s average length ~ 83,000 games created and finished per second
```

Demand turns over roughly every minute, so the fleet must react within seconds. That churn rate also settles two design questions definitively. A container or channel per room is out of the question — Pod scheduling costs far more than a 30-to-90-second room lifetime — so the unit of scale is one long-lived instance hosting thousands of rooms. And the likely bottleneck is not move computation but the **coordination write path**: generating a room id, choosing an owner and writing the lease happens ~83,000 times a second, once per room rather than once per move.

For the **API tier**, requests/sec and p99 latency. For **persistence workers**, broker queue depth.

### 11.3 Persistence must not block a game

`GameRoom._settle_elo_for_game_end` currently awaits the database write in the room's own shutdown path. At 83,000 finished games per second, a synchronous write there is untenable, and a slow database would stall rooms over an operation that has nothing to do with the game.

Decouple it. On game end, the authority publishes a `game-finished` event to the **durable** stream and frees the room without waiting for a database acknowledgment. A pool of stateless persistence workers consumes that stream and performs the writes in batches, amortizing many small writes into fewer large ones. Because the stream is durable, a database slowdown queues messages in the broker rather than backing pressure up into the simulation. Delivery is at-least-once, which is exactly why the `ON CONFLICT (room_id) DO NOTHING` change from Step 3 is a prerequisite rather than a refinement: a redelivered event must be a no-op, not a duplicate game.

### 11.4 Split the data stores by shape

One database for everything stops working before any single store does.

- **Users, auth, ELO** — needs real ACID guarantees for a unique username, a password change, an atomic rating update. PostgreSQL with one primary and read replicas. 100M rows is comfortable in one table; when write and connection *throughput* becomes the limit, shard by `user_id`.
- **Presence and session mapping** — Redis. Very low latency, no strong durability requirement, since a session is rebuilt on reconnect.
- **Game and move history** — append-only and continuously written at high volume. A wide-column store (Cassandra or ScyllaDB) or object storage, not a relational database. This is the `games` and `moves` data, and it is what the CDN in Step 4 caches on read.
- **Leaderboard** — never a live `ORDER BY` over 100M rows. A Redis sorted set updated incrementally as ratings change, which is the same structure the matchmaking queue uses.

### 11.5 The traffic this produces

Assuming one move per player per two seconds and the 123-byte event frame measured in Section 2, inbound is 5,000,000 events/second at roughly 150–250 bytes with framing and TLS overhead, about 1 GB/s or 8 Gbps. Each event fans out to the opponent and any spectators, roughly ×2–3, giving 10–15M deliveries/second and another 2–3 GB/s. Total aggregate is on the order of 20–30 Gbps.

In aggregate that is a large real-time infrastructure. Per container it collapses to nothing: 20,000 players at one move per two seconds is 10,000 messages/second at ~150 bytes, about 1.5 MB/s. Every room is independent of every other, so this traffic shards naturally across gateways and authorities with no coordination — unlike room ownership, which needs the lease mechanism in Step 7 precisely because it does *not* shard for free. The point of horizontal scaling is never the total; it is that no single container absorbs more than the slice it was sized for.

**Exit criterion:** replica counts track load without manual intervention; a killed instance is replaced automatically; a deliberately stalled database causes the broker queue to grow and the persistence-worker replica count to rise, while games continue unaffected.

---

## 12. CI/CD, end state

The pipeline is the mechanism that makes every step above deployable, and it grows with them.

On every pull request: run pytest (the same suite the sole pre-commit hook runs), build the image, run a dependency and image vulnerability scan, and bring up the Compose stack to run `tests/integration/test_ws_server.py` against a real socket.

On merge to `main`: build and push the image tagged with the commit SHA — never `latest`, because a mutable tag makes a rollout unreproducible and a rollback ambiguous. Run the Alembic migration job to completion. Then update the Deployment's image tag, which is the entire deployment action.

Rollouts are ordered by blast radius. The API tier deploys freely — a restart drops nothing, since clients retry. The gateway tier deploys rarely and always with draining, since every client on a replaced instance must reconnect. The game-authority tier uses the `preStop` drain from Step 7 and a surge-then-drain rolling update, so capacity exists to take new rooms before old instances stop accepting them.

Migrations are always backward-compatible with the currently running version — add columns before writing them, stop reading a column before dropping it — so that a rollback never needs a schema rollback.

A rollback is a redeploy of the previous SHA. Because the image is immutable and the migration is compatible in both directions, that is a complete recovery action.

---

## 13. What is measured, and what each measurement decides

Every number in Sections 2 and 11 is arithmetic. The measurements below are what turn them into facts, and the autoscalers are *driven* by them — a metrics pipeline that lies produces a fleet that scales wrongly.

| Role | Metrics | What it decides |
|---|---|---|
| WS Gateway | Open connections, connections/sec, inbound frames/sec, socket-write queue depth | Drives the HPA; connection count is what replaces the assumed per-container capacity |
| API Gateway | Requests/sec, p99 latency, error rate per endpoint | Standard RED metrics; a slow login is invisible in game metrics |
| Game Authority | Active rooms, **tick duration p99**, moves/sec, rooms gained and lost per second | Tick duration is the real health signal: a tick overrunning its 50 ms budget means the simulation is behind wall-clock, which no CPU percentage reveals |
| Ownership layer | Lease acquisitions/sec, renewal failures, failover count | A rising failover count means leases are being lost to network jitter rather than to crashes |
| Broker | Publish rate, subscriber lag, dropped messages | Tests whether Step 6's "loss is tolerable" assumption is actually being exercised |
| Persistence workers | Queue depth, batch size, write latency, retries | Queue depth drives the HPA; sustained growth means the database, not the game, is the bottleneck |

One end-to-end measurement outranks all of them: **move-to-render latency**, from a client publishing a move to the opponent's client receiving the event. It spans every hop and is the only number that corresponds to something a player can feel. It carries an explicit SLO of p99 under 150 ms in-region, and a deploy is permitted to violate it zero times.

Four load tests convert the estimates into measurements, and each has a defined consequence:

1. **Single-container capacity** — drive one gateway with synthetic clients until latency degrades. The result replaces the assumed capacity and re-derives every replica count.
2. **Scaling latency** — ramp past the HPA threshold and measure how long new capacity takes to serve traffic. If that lag exceeds the ramp rate of real traffic, the autoscaling policy is wrong regardless of the eventual replica count.
3. **Failure injection** — kill a game authority holding live rooms and confirm the lease expiry, takeover and bounded blast radius from Step 7.
4. **Churn** — sustain room creation and destruction at 83,000/second. This exercises the coordination write path, which Section 11.2 predicts will fail before move computation does.

The load generator is cheap to build here because the game already has a scripted headless driver: the `.kfc` text-script runner in `core/texttests/` pointed at a socket instead of an in-process `GameService`.

---

## 14. Target topology

```
                              Clients
                                 │
                          CDN (static + immutable replays)
                                 │
                         Load Balancer / Ingress  ── TLS terminates here
                                 │
              ┌──────────────────┴──────────────────┐
              │ HTTPS                               │ WSS
     ┌────────┴────────┐                   ┌────────┴────────┐
  API Gateway     API Gateway            WS Gateway      WS Gateway
  (token issue,   (replays,              (token verify,  (socket only)
   leaderboard)    history)               forward only)
        │                                          │
        │                                  Broker: room.*.events   (NATS core)
        │                                          │
        │                       ┌──────────────────┼──────────────────┐
        │                  Game Auth.         Game Auth.         Game Auth.
        │                  (core engine,      (leases from Redis)
        │                   thousands of rooms each)
        │                                          │
        │                                  Broker: game.finished    (JetStream, durable)
        │                                          │
        │                                  Persistence Workers
        │                                          │
        └──────────────┬───────────────────────────┘
                       │
        PostgreSQL (users, ELO)  ·  Redis (presence, leases, queue, leaderboard)
                   ·  Cassandra / object storage (game + move history)
```

The organizing principle is that each component does exactly one job — hold connections, run games, match players, persist results — and nothing else. That is what allows any one of them to be replicated independently of the others, and it is why the system can grow from hundreds of users to hundreds of millions without a single change to the game's rules. `core/` is the same library in Step 1 and in Step 8.

---

## 15. Decisions recorded

These are settled, so that no later step has to reopen them.

**A crash loses its in-flight games.** Games last 30 to 90 seconds. Affected players are reconnected into a fresh game rather than having the position reconstructed from checkpoints. Leases bound the blast radius; they do not preserve games.

**TLS terminates at the ingress**, not in the application. The application reads `X-Forwarded-For` and trusts it only from the ingress.

**Tokens are issued by the API tier and only verified by the WebSocket tier.** The socket tier never reads the user table.

**bcrypt stays**, at an explicitly pinned cost, executed off the event loop, with rehash-on-login when the stored cost is below target.

**ELO writes stay absolute, never incremental**, because that is what makes them replay-safe.

**Image tags are commit SHAs.** `latest` is never deployed.

**The event stream is fire-and-forget; the persistence stream is durable.** One broker technology for each, chosen from the failure tolerance rather than from familiarity.

**Matchmaking prefers same-region pairing**, falling back to cross-region after a bounded wait, and placing the room where the sum of both players' round-trip times is smallest.

**Reconnect is served by a Redis `user_id → room_id` mapping** with a TTL matched to the game's expected lifetime, readable from any gateway, so returning to the same instance is never required.
