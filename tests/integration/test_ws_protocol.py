"""Integration tests for the wire protocol and edge controls, over a real socket.

These cover the changes that only mean anything end to end: that the server has
stopped broadcasting a full board on every tick, that one player's selection
never reaches the other, that a repeated move frame is not executed twice, that
the heartbeat closes a socket which stops answering, and that a token issued by
the HTTP tier is accepted by the WebSocket tier without a password.
"""

import asyncio
import json

import pytest
import pytest_asyncio
import websockets

from server.application.auth_service import AuthService
from server.application.token_service import TokenService
from server.domain.matchmaking.queue import MatchmakingQueue
from server.infrastructure.database.database import Database
from server.presentation.connection_guard import ConnectionGuard
from server.presentation.ws_server import KFChessServer

_TEST_PASSWORD = "password123"
_CLOSE_TIMEOUT = 0.3
_RECV_TIMEOUT = 3.0
_NO_BOT_FALLBACK_TIMEOUT = 600.0
_SIGNING_KEY = "integration-test-key"

# Long enough that the heartbeat never fires in tests that are not about it.
_IDLE_HEARTBEAT_SECONDS = 600.0


def _connect(port: int):
    return websockets.connect(f"ws://localhost:{port}", close_timeout=_CLOSE_TIMEOUT)


async def _send_json(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload))


async def _recv_json(ws, timeout: float = _RECV_TIMEOUT) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def _recv_until(ws, msg_type: str, timeout: float = _RECV_TIMEOUT) -> dict:
    """Receive frames until one of *msg_type* arrives, answering pings on the way.

    A real client answers the server's heartbeat, so a test client that does not
    would be closed as half-open mid-test.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"Timed out waiting for {msg_type!r}")
        msg = await _recv_json(ws, timeout=remaining)
        if msg.get("type") == "ping":
            await _send_json(ws, {"type": "pong"})
            continue
        if msg.get("type") == msg_type:
            return msg


async def _collect_for(ws, seconds: float) -> list:
    """Every frame that arrives within *seconds*, answering pings."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    frames = []
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return frames
        try:
            msg = await _recv_json(ws, timeout=remaining)
        except asyncio.TimeoutError:
            return frames
        if msg.get("type") == "ping":
            await _send_json(ws, {"type": "pong"})
            continue
        frames.append(msg)


async def _authenticate(ws, username: str, action: str = "register") -> dict:
    await _send_json(
        ws, {"type": "auth", "action": action, "username": username, "password": _TEST_PASSWORD}
    )
    reply = await _recv_json(ws)
    assert reply["type"] == "auth" and reply.get("status") == "ok", reply
    return reply


async def _play(ws, username: str, action: str = "register") -> None:
    await _authenticate(ws, username, action=action)
    await _send_json(ws, {"type": "play"})


async def _seated_pair(port):
    """Two authenticated, matched clients with their game already started."""
    white_ws = await _connect(port).__aenter__()
    black_ws = await _connect(port).__aenter__()
    await _play(white_ws, "ProtoWhite")
    await _play(black_ws, "ProtoBlack")
    white_start = await _recv_until(white_ws, "game_start")
    await _recv_until(black_ws, "game_start")
    if white_start["color"] != "w":
        white_ws, black_ws = black_ws, white_ws
    return white_ws, black_ws


@pytest_asyncio.fixture
async def server_factory(tmp_path):
    """Builds throwaway servers, each with its own database and port."""
    started = []

    async def _factory(port: int, **kwargs) -> KFChessServer:
        db = Database(str(tmp_path / f"proto_{port}.db"))
        await db.connect()
        kwargs.setdefault("matchmaker", MatchmakingQueue(timeout_seconds=_NO_BOT_FALLBACK_TIMEOUT))
        kwargs.setdefault("heartbeat_interval_seconds", _IDLE_HEARTBEAT_SECONDS)
        server = KFChessServer(
            host="localhost", port=port, database=db, auth_service=AuthService(db), **kwargs
        )
        server._test_database = db
        await server.start()
        started.append(server)
        return server

    yield _factory

    for server in started:
        await server.stop()
        await server._test_database.close()


@pytest.mark.asyncio
async def test_an_idle_room_does_not_broadcast_a_snapshot_every_tick(server_factory):
    """The runner ticks 20 times a second. It used to send a ~5.5 KB board to
    every recipient on each of them — ~219 KB/s for a two-player room, whether
    or not anything changed. Steady state must now be quiet.
    """
    port = 8801
    await server_factory(port, reconciliation_interval_seconds=60.0)

    white_ws, black_ws = await _seated_pair(port)
    try:
        # Drain the opening snapshot and start frames.
        await _recv_until(white_ws, "game_state")

        idle_frames = await _collect_for(white_ws, seconds=1.0)
        snapshots = [f for f in idle_frames if f.get("type") == "game_state"]

        # At 20 Hz the old server would have sent ~20 here.
        assert snapshots == [], f"idle room still broadcasting: {len(snapshots)} snapshots"
    finally:
        await white_ws.close()
        await black_ws.close()


@pytest.mark.asyncio
async def test_a_full_snapshot_still_opens_the_game(server_factory):
    """A client's first board cannot be an event stream — it has nothing to
    apply events to."""
    port = 8802
    await server_factory(port, reconciliation_interval_seconds=60.0)

    white_ws, black_ws = await _seated_pair(port)
    try:
        opening = await _recv_until(white_ws, "game_state")

        assert len(opening["state"]["pieces"]) == 32
        assert opening["state"]["rows"] == 8
    finally:
        await white_ws.close()
        await black_ws.close()


@pytest.mark.asyncio
async def test_the_reconciliation_frame_repairs_drift_periodically(server_factory):
    """A lost event must cost seconds of drift, not a permanently desynchronized
    game — which is also what makes a fire-and-forget event transport safe."""
    port = 8803
    await server_factory(port, reconciliation_interval_seconds=0.3)

    white_ws, black_ws = await _seated_pair(port)
    try:
        await _recv_until(white_ws, "game_state")

        frames = await _collect_for(white_ws, seconds=1.2)
        snapshots = [f for f in frames if f.get("type") == "game_state"]

        assert 1 <= len(snapshots) <= 8, f"unexpected reconciliation rate: {len(snapshots)}"
    finally:
        await white_ws.close()
        await black_ws.close()


@pytest.mark.asyncio
async def test_a_move_produces_events_rather_than_a_snapshot(server_factory):
    """`MoveStartedEvent` carries from/to/arrival_ms — everything a client needs
    to draw the travel itself, at ~123 bytes instead of ~5,480."""
    port = 8804
    await server_factory(port, reconciliation_interval_seconds=60.0)

    white_ws, black_ws = await _seated_pair(port)
    try:
        await _recv_until(white_ws, "game_state")
        await _send_json(white_ws, {"type": "move", "from": "e2", "to": "e4"})

        started = await _recv_until(black_ws, "event_move_started")

        assert started["from"] == "e2"
        assert started["to"] == "e4"
        assert "arrival_ms" in started and "at_ms" in started
    finally:
        await white_ws.close()
        await black_ws.close()


@pytest.mark.asyncio
async def test_one_players_selection_never_reaches_the_other(server_factory):
    """The snapshot carries `selected_pos` and the squares highlighted for the
    player who selected — sending that to the opponent leaks intent."""
    port = 8805
    await server_factory(port, reconciliation_interval_seconds=0.3)

    white_ws, black_ws = await _seated_pair(port)
    try:
        await _recv_until(white_ws, "game_state")
        await _send_json(white_ws, {"type": "move", "from": "e2", "to": "e4"})

        black_state = await _recv_until(black_ws, "game_state", timeout=4.0)

        assert black_state["state"]["selected_pos"] is None
        assert black_state["state"]["legal_move_targets"] == []
        assert black_state["state"]["castle_targets"] == []
    finally:
        await white_ws.close()
        await black_ws.close()


@pytest.mark.asyncio
async def test_a_repeated_move_id_is_not_executed_twice(server_factory):
    """A client that retries after a flaky reconnect must not move the same
    piece twice — in real-time chess that is a second, unintended motion."""
    port = 8806
    server = await server_factory(port, reconciliation_interval_seconds=60.0)

    white_ws, black_ws = await _seated_pair(port)
    try:
        start = await _recv_until(white_ws, "game_state")
        assert start
        move = {"type": "move", "from": "e2", "to": "e4", "move_id": "retry-me"}

        await _send_json(white_ws, move)
        await _recv_until(black_ws, "event_move_started")
        await _send_json(white_ws, move)

        # The retry produces no second departure from e2.
        frames = await _collect_for(black_ws, seconds=1.0)
        repeats = [
            f for f in frames
            if f.get("type") == "event_move_started" and f.get("from") == "e2"
        ]
        assert repeats == []

        room = next(iter(server.room_manager.all_rooms()))
        assert room is not None
    finally:
        await white_ws.close()
        await black_ws.close()


@pytest.mark.asyncio
async def test_a_move_frame_with_a_non_string_move_id_is_refused(server_factory):
    """It becomes a cache key, so it is validated rather than trusted."""
    port = 8807
    await server_factory(port, reconciliation_interval_seconds=60.0)

    white_ws, black_ws = await _seated_pair(port)
    try:
        await _send_json(white_ws, {"type": "move", "from": "e2", "to": "e4", "move_id": 17})

        error = await _recv_until(white_ws, "error")

        assert "move_id" in error["message"]
    finally:
        await white_ws.close()
        await black_ws.close()


@pytest.mark.asyncio
async def test_the_server_pings_and_closes_a_socket_that_never_answers(server_factory):
    """A socket whose network vanishes without a TCP FIN is otherwise noticed
    only when a write raises — and a write to a disconnected session returns
    silently, so the seat could sit occupied until the game ended on its own.
    """
    port = 8808
    await server_factory(port, heartbeat_interval_seconds=0.2, heartbeat_timeout_seconds=0.2)

    async with _connect(port) as ws:
        # A session joins the heartbeat's watch only once it is established,
        # which takes the post-auth handshake frame as well as the auth itself.
        await _play(ws, "Silent")

        saw_ping = False
        with pytest.raises((websockets.ConnectionClosed, asyncio.TimeoutError)):
            for _ in range(50):
                frame = await _recv_json(ws, timeout=2.0)
                saw_ping = saw_ping or frame.get("type") == "ping"

        assert saw_ping, "server never sent a heartbeat ping"


@pytest.mark.asyncio
async def test_answering_pings_keeps_a_socket_alive(server_factory):
    """The other half of the contract: a client that pongs is never dropped."""
    port = 8809
    server = await server_factory(
        port, heartbeat_interval_seconds=0.2, heartbeat_timeout_seconds=0.3
    )

    async with _connect(port) as ws:
        await _authenticate(ws, "Chatty")
        await _send_json(ws, {"type": "play"})

        await _collect_for(ws, seconds=1.5)  # answers every ping it sees

        assert server.session_count == 1
        await _send_json(ws, {"type": "ping"})
        assert (await _recv_until(ws, "pong"))["type"] == "pong"


@pytest.mark.asyncio
async def test_a_token_authenticates_a_socket_without_a_password(server_factory):
    """Once the API tier has issued a token, a socket costs a signature check
    rather than a bcrypt verification — and needs no user-table read at all."""
    port = 8810
    tokens = TokenService(signing_key=_SIGNING_KEY)
    server = await server_factory(port, token_service=tokens)
    await server._test_database.create_user("Bearer", _TEST_PASSWORD, initial_elo=1337)

    async with _connect(port) as ws:
        await _send_json(
            ws, {"type": "auth", "token": tokens.issue(1, "Bearer", 1337, "access")}
        )
        reply = await _recv_json(ws)

    assert reply["type"] == "auth"
    assert reply["status"] == "ok"
    assert reply["username"] == "Bearer"
    assert reply["elo"] == 1337


@pytest.mark.asyncio
async def test_a_token_signed_with_another_key_is_refused(server_factory):
    port = 8811
    await server_factory(port, token_service=TokenService(signing_key=_SIGNING_KEY))
    forged = TokenService(signing_key="not-the-servers-key").issue(1, "Impostor", 1200, "access")

    async with _connect(port) as ws:
        await _send_json(ws, {"type": "auth", "token": forged})
        reply = await _recv_json(ws)

    assert reply["type"] == "error"


@pytest.mark.asyncio
async def test_frames_beyond_the_per_socket_budget_are_dropped_with_an_error(server_factory):
    """Move frames flow into an unbounded queue, so an unlimited frame rate is
    an unlimited queue."""
    port = 8812
    await server_factory(
        port, guard=ConnectionGuard(frames_per_second=1.0, frame_burst=5)
    )

    async with _connect(port) as ws:
        await _authenticate(ws, "Flooder")
        for _ in range(20):
            await _send_json(ws, {"type": "ping"})

        frames = await _collect_for(ws, seconds=1.0)
        errors = [f for f in frames if f.get("type") == "error"]

        assert errors, "flood was never rate limited"
        assert any("rate limit" in f["message"].lower() for f in errors)


@pytest.mark.asyncio
async def test_a_user_cannot_hold_more_than_their_room_allowance(server_factory):
    """Room creation is otherwise unbounded per user, and every live room holds
    an engine and a tick loop."""
    port = 8813
    await server_factory(port, guard=ConnectionGuard(concurrent_rooms_per_user=1))

    async with _connect(port) as first_ws:
        await _authenticate(first_ws, "Hoarder")
        await _send_json(first_ws, {"type": "create_room"})
        await _recv_until(first_ws, "room_created")

        async with _connect(port) as second_ws:
            await _authenticate(second_ws, "Hoarder", action="login")
            await _send_json(second_ws, {"type": "create_room"})

            error = await _recv_until(second_ws, "error")
            assert "open rooms" in error["message"]


@pytest.mark.asyncio
async def test_connections_beyond_the_per_ip_budget_are_refused(server_factory):
    """Every auth attempt costs a bcrypt verification, so unlimited connections
    from one host is unlimited CPU from one host."""
    port = 8814
    await server_factory(port, guard=ConnectionGuard(connections_per_minute=2))

    async with _connect(port) as first, _connect(port) as second:
        await _authenticate(first, "IpOne")
        await _authenticate(second, "IpTwo")

        async with _connect(port) as third:
            reply = await _recv_json(third)
            assert reply["type"] == "error"
            assert "too many connections" in reply["message"].lower()
