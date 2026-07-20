"""Integration tests for the WebSocket server's lobby, rooms, and game flow.

Covers the Phase C contract: a `play` frame queues the client for ELO-bounded
matchmaking rather than seating it immediately, so the first player waits until
either a compatible opponent queues or the queue timeout hands them a bot.
"""

import asyncio
import json
import pytest
import pytest_asyncio
import websockets

from server.application.auth_service import AuthService
from server.domain.matchmaking.queue import MatchmakingQueue
from server.infrastructure.database.database import Database
from server.presentation.ws_server import KFChessServer
from shared.events import GameEndedEvent

_RECV_TIMEOUT = 2.0
_TEST_PASSWORD = "password123"
_CLOSE_TIMEOUT = 0.3

# Long enough that pairing always wins the race in tests that expect a human
# opponent, so the bot fallback never fires unless a test opts into it.
_NO_BOT_FALLBACK_TIMEOUT = 600.0


def _connect(port: int):
    """Open a client socket that does not linger on close.

    These tests deliberately leave 20Hz tick broadcasts unread, and the client's
    default 10s close_timeout makes every such socket stall for that full
    timeout while its backlog drains — 40s of pure teardown in a four-client
    test. Server-side close behavior is unaffected.
    """
    return websockets.connect(f"ws://localhost:{port}", close_timeout=_CLOSE_TIMEOUT)


async def _recv_json(ws, timeout: float = _RECV_TIMEOUT):
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


async def _send_json(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload))


async def _authenticate(ws, username: str, action: str = "register", password: str = _TEST_PASSWORD) -> dict:
    """Perform the mandatory auth handshake a real client always opens with."""
    await _send_json(ws, {"type": "auth", "action": action, "username": username, "password": password})
    reply = await _recv_json(ws)
    assert reply["type"] == "auth" and reply.get("status") == "ok", reply
    return reply


async def _recv_until(ws, msg_type: str, timeout: float = 3.0):
    """Receive frames until one of `msg_type` arrives, skipping the periodic
    20Hz `game_state`/`event_*` tick broadcasts that interleave with it once
    the room's runner is live.

    Needed even for `game_start`: seating the second player initializes the
    game synchronously, so the resulting `event_game_started` broadcast can
    reach the socket before the handshake reply does.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"Timed out waiting for {msg_type!r}")
        msg = await _recv_json(ws, timeout=remaining)
        if msg.get("type") == msg_type:
            return msg


async def _assert_never_receives(ws, msg_type: str, timeout: float = 1.0) -> None:
    """Assert `msg_type` does not arrive within `timeout`, ignoring other frames."""
    try:
        msg = await _recv_until(ws, msg_type, timeout=timeout)
    except asyncio.TimeoutError:
        return
    pytest.fail(f"Unexpectedly received {msg_type!r}: {msg}")


async def _play(ws, username: str, action: str = "register"):
    """Authenticate and enter the matchmaking queue."""
    await _authenticate(ws, username, action=action)
    await _send_json(ws, {"type": "play"})


@pytest_asyncio.fixture
async def auth_server(tmp_path):
    """A running KFChessServer wired to a real, throwaway Database + AuthService."""

    servers = []

    async def _factory(port: int, **kwargs) -> KFChessServer:
        db = Database(str(tmp_path / f"test_{port}.db"))
        await db.connect()
        kwargs.setdefault("matchmaker", MatchmakingQueue(timeout_seconds=_NO_BOT_FALLBACK_TIMEOUT))
        server = KFChessServer(
            host="localhost", port=port, database=db, auth_service=AuthService(db), **kwargs
        )
        server._test_database = db  # keep a handle for teardown
        await server.start()
        servers.append(server)
        return server

    yield _factory

    for server in servers:
        await server.stop()
        await server._test_database.close()


@pytest.mark.asyncio
async def test_matchmaking_pairs_two_players_into_a_room(auth_server):
    """A lone `play` frame waits in the queue; the second one pairs them both
    into a freshly allocated room with complementary colors.
    """
    port = 8766
    await auth_server(port)

    async with _connect(port) as ws1:
        await _play(ws1, "Alice")

        # Nobody to pair with yet — the old behavior seated Alice immediately.
        await _assert_never_receives(ws1, "game_start")

        async with _connect(port) as ws2:
            await _play(ws2, "Bob")

            start1 = await _recv_until(ws1, "game_start")
            start2 = await _recv_until(ws2, "game_start")

            assert start1["color"] == "w"
            assert start2["color"] == "b"
            assert start1["opponent"] == "Bob"
            assert start2["opponent"] == "Alice"
            assert start1["room_id"] == start2["room_id"]
            assert len(start1["room_id"]) == 6


@pytest.mark.asyncio
async def test_matched_players_can_move_and_receive_state(auth_server):
    port = 8771
    await auth_server(port)

    async with _connect(port) as ws1:
        await _play(ws1, "Mover")
        async with _connect(port) as ws2:
            await _play(ws2, "Waiter")
            await _recv_until(ws1, "game_start")
            await _recv_until(ws2, "game_start")

            await _send_json(ws1, {"type": "move", "from": "e2", "to": "e4"})

            received_types = []
            for _ in range(5):
                try:
                    received_types.append((await _recv_json(ws1, timeout=1.0)).get("type"))
                except asyncio.TimeoutError:
                    break

            assert any(
                t in received_types
                for t in ("game_state", "event_move_started", "event_piece_moved")
            ), received_types


@pytest.mark.asyncio
async def test_game_end_broadcasts_new_elo_and_elo_change_for_both_players(auth_server):
    """A natural game end must hand each player their updated rating alongside
    the result — not just who won — so the client can show it without a
    separate round trip. Triggered by publishing GameEndedEvent directly onto
    the room's bus, the same seam server/unit tests use, since driving a real
    game to checkmate over the wire buys nothing extra here.
    """
    port = 8780
    server = await auth_server(port)

    async with _connect(port) as ws1, _connect(port) as ws2:
        await _play(ws1, "Rater")
        await _play(ws2, "Ratee")
        start1 = await _recv_until(ws1, "game_start")
        await _recv_until(ws2, "game_start")

        room = server.room_manager.get_room(start1["room_id"])
        room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))
        await room._elo_settlement_task

        white_end = await _recv_until(ws1 if start1["color"] == "w" else ws2, "game_end")
        black_end = await _recv_until(ws2 if start1["color"] == "w" else ws1, "game_end")

        for end in (white_end, black_end):
            assert end["reason"] == "checkmate"
            assert end["winner"] == "w"
            assert set(end["white"]) == {"new_elo", "elo_change"}
            assert set(end["black"]) == {"new_elo", "elo_change"}

        # White won: rating rises for White, falls for Black — both starting at 1200.
        assert white_end["white"]["elo_change"] > 0
        assert white_end["black"]["elo_change"] < 0
        assert white_end["white"]["new_elo"] == 1200 + white_end["white"]["elo_change"]
        assert white_end["black"]["new_elo"] == 1200 + white_end["black"]["elo_change"]

        # Wait for the room's own reap so its background tasks are gone before
        # this test's context managers tear the sockets down underneath it.
        await room._expiry_task


@pytest.mark.asyncio
async def test_elo_gap_beyond_bound_blocks_pairing(auth_server):
    """Players more than 100 rating points apart must not be paired, while a
    third player inside the bound pairs immediately.
    """
    port = 8772
    server = await auth_server(port)
    db = server._test_database

    # Seeded directly with the ratings this test matches on, so each session
    # carries a known ELO from the moment it logs in.
    await db.create_user("Novice", _TEST_PASSWORD, initial_elo=1200)
    await db.create_user("Master", _TEST_PASSWORD, initial_elo=1500)
    await db.create_user("Peer", _TEST_PASSWORD, initial_elo=1250)

    async with _connect(port) as ws_novice:
        await _play(ws_novice, "Novice", action="login")

        async with _connect(port) as ws_master:
            await _play(ws_master, "Master", action="login")

            # 300 points apart — outside the +/-100 bound.
            await _assert_never_receives(ws_novice, "game_start")
            assert server.matchmaker.queue_length == 2

            async with _connect(port) as ws_peer:
                await _play(ws_peer, "Peer", action="login")

                start_novice = await _recv_until(ws_novice, "game_start")
                start_peer = await _recv_until(ws_peer, "game_start")
                assert start_novice["opponent"] == "Peer"
                assert start_peer["opponent"] == "Novice"

                # The out-of-band player is still waiting, not dragged in.
                await _assert_never_receives(ws_master, "game_start")


@pytest.mark.asyncio
async def test_queue_timeout_seats_a_bot_that_actually_plays(auth_server):
    """Timing out of the queue must not dead-end the player: a bot takes the
    opposing seat and starts making its own moves.
    """
    port = 8773
    await auth_server(
        port,
        matchmaker=MatchmakingQueue(timeout_seconds=0.4),
        bot_move_interval_seconds=0.2,
    )

    async with _connect(port) as ws:
        await _play(ws, "Lonely")

        start = await _recv_until(ws, "game_start", timeout=5.0)
        assert start["color"] == "w"
        assert start["opponent"] == "KungFuBot"
        assert len(start["room_id"]) == 6

        # The human never moves, so any black motion is the bot playing.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 8.0
        while True:
            remaining = deadline - loop.time()
            assert remaining > 0, "Bot was seated but never made a move"
            msg = await _recv_json(ws, timeout=remaining)
            if msg.get("type") in ("event_move_started", "event_piece_moved"):
                if msg.get("color") == "b":
                    break


@pytest.mark.asyncio
async def test_create_and_join_named_room(auth_server):
    port = 8774
    await auth_server(port)

    async with _connect(port) as host_ws:
        await _authenticate(host_ws, "Host")
        await _send_json(host_ws, {"type": "create_room"})

        created = await _recv_until(host_ws, "room_created")
        room_id = created["room_id"]
        assert len(room_id) == 6
        assert room_id.isalnum() and room_id.isupper()

        # A private room must not pull an unrelated queued player into it.
        await _assert_never_receives(host_ws, "game_start")

        async with _connect(port) as guest_ws:
            await _authenticate(guest_ws, "Guest")
            await _send_json(guest_ws, {"type": "join_room", "room_id": room_id})

            host_start = await _recv_until(host_ws, "game_start")
            guest_start = await _recv_until(guest_ws, "game_start")

            assert host_start["color"] == "w"
            assert guest_start["color"] == "b"
            assert host_start["room_id"] == room_id
            assert guest_start["room_id"] == room_id
            assert guest_start["opponent"] == "Host"


@pytest.mark.asyncio
async def test_joining_unknown_room_is_rejected(auth_server):
    port = 8775
    await auth_server(port)

    async with _connect(port) as ws:
        await _authenticate(ws, "Wanderer")
        await _send_json(ws, {"type": "join_room", "room_id": "ZZZZZZ"})
        err = await _recv_until(ws, "error")
        assert "ZZZZZZ" in err["message"]


@pytest.mark.asyncio
async def test_third_joiner_spectates_and_cannot_move(auth_server):
    """A full room slots further joiners as passive viewers: they receive
    broadcast state but their move frames are refused.
    """
    port = 8776
    await auth_server(port)

    async with _connect(port) as host_ws:
        await _authenticate(host_ws, "RoomHost")
        await _send_json(host_ws, {"type": "create_room"})
        room_id = (await _recv_until(host_ws, "room_created"))["room_id"]

        async with _connect(port) as guest_ws:
            await _authenticate(guest_ws, "RoomGuest")
            await _send_json(guest_ws, {"type": "join_room", "room_id": room_id})
            await _recv_until(guest_ws, "game_start")

            async with _connect(port) as viewer_ws:
                await _authenticate(viewer_ws, "Watcher")
                await _send_json(viewer_ws, {"type": "join_room", "room_id": room_id})

                # The client only opens its game window off a game_start frame
                # (the Lobby's poll loop matches on "game_start"/"room_created"
                # exclusively) — without this, a spectator's client hangs
                # forever waiting for a frame that never arrives.
                start = await _recv_until(viewer_ws, "game_start")
                assert start["color"] == "viewer"
                assert start["room_id"] == room_id

                info = await _recv_until(viewer_ws, "info")
                assert "spectator" in info["message"].lower()

                # Spectators are on the broadcast path...
                await _recv_until(viewer_ws, "game_state")

                # ...but are not allowed to influence the game.
                await _send_json(viewer_ws, {"type": "move", "from": "e2", "to": "e4"})
                err = await _recv_until(viewer_ws, "error")
                assert "spectator" in err["message"].lower()


@pytest.mark.asyncio
async def test_concurrent_rooms_stay_isolated(auth_server):
    """Two matched pairs run in separate rooms simultaneously, and a move in
    one room is never broadcast into the other.
    """
    port = 8777
    server = await auth_server(port)

    async with _connect(port) as a1, \
            _connect(port) as a2:
        await _play(a1, "RoomAWhite")
        await _play(a2, "RoomABlack")
        start_a1 = await _recv_until(a1, "game_start")
        start_a2 = await _recv_until(a2, "game_start")

        async with _connect(port) as b1, \
                _connect(port) as b2:
            await _play(b1, "RoomBWhite")
            await _play(b2, "RoomBBlack")
            start_b1 = await _recv_until(b1, "game_start")
            start_b2 = await _recv_until(b2, "game_start")

            assert start_a1["room_id"] == start_a2["room_id"]
            assert start_b1["room_id"] == start_b2["room_id"]
            assert start_a1["room_id"] != start_b1["room_id"]
            assert server.room_manager.room_count == 2
            assert start_b1["opponent"] == "RoomBBlack"

            # Both rooms tick independently.
            await _recv_until(a1, "game_state")
            await _recv_until(b1, "game_state")

            # A move in room B reaches B's opponent, never A's players.
            await _send_json(b1, {"type": "move", "from": "d2", "to": "d4"})
            await _recv_until(b2, "event_move_started")
            await _assert_never_receives(a2, "event_move_started")


@pytest.mark.asyncio
async def test_cancel_search_prevents_pairing(auth_server):
    """Withdrawing from the queue means a later player cannot be paired with
    the departed one.
    """
    port = 8778
    server = await auth_server(port)

    async with _connect(port) as ws1:
        await _play(ws1, "Quitter")
        await _send_json(ws1, {"type": "cancel_search"})
        await asyncio.sleep(0.2)
        assert server.matchmaker.queue_length == 0

        async with _connect(port) as ws2:
            await _play(ws2, "Hopeful")
            await _assert_never_receives(ws1, "game_start")
            await _assert_never_receives(ws2, "game_start")
            assert server.matchmaker.queue_length == 1


@pytest.mark.asyncio
async def test_disconnect_while_queued_prevents_phantom_pairing(auth_server):
    """A player who drops while waiting is evicted from the queue, so the next
    arrival is not paired into a room nobody is listening to.
    """
    port = 8779
    server = await auth_server(port)

    async with _connect(port) as ws1:
        await _play(ws1, "Ghosted")
        await asyncio.sleep(0.2)
        assert server.matchmaker.queue_length == 1

    await asyncio.sleep(0.3)  # let the server finish tearing the socket down
    assert server.matchmaker.queue_length == 0

    async with _connect(port) as ws2:
        await _play(ws2, "Arrival")
        await _assert_never_receives(ws2, "game_start")
        assert server.room_manager.room_count == 0


@pytest.mark.asyncio
async def test_reconnect_preserves_seat_and_syncs_state(auth_server):
    """A dropped player's seat survives the disconnect, and reconnecting with
    their authenticated identity rebinds them onto it with a full state
    sync — instead of being dropped or slotted in fresh as a spectator.
    """
    port = 8767
    await auth_server(port)

    async with _connect(port) as ws1:
        await _play(ws1, "White1")

        async with _connect(port) as ws2:
            await _play(ws2, "Black1")

            start2 = await _recv_until(ws2, "game_start")
            white_username = start2["opponent"]
            await _recv_until(ws1, "game_start")

            # White drops the connection mid-game.
            await ws1.close()

            # Black is notified that White disconnected and a countdown started.
            notice = await _recv_until(ws2, "opponent_disconnected")
            assert notice["username"] == white_username

            # White reconnects with a fresh socket, re-authenticating (login,
            # since the account already exists) before the same identity.
            async with _connect(port) as ws1b:
                await _authenticate(ws1b, white_username, action="login")
                await _send_json(ws1b, {"type": "reconnect", "username": white_username})

                sync_msg = await _recv_until(ws1b, "game_state")
                assert "state" in sync_msg

                reconnected_notice = await _recv_until(ws2, "opponent_reconnected")
                assert reconnected_notice["username"] == white_username


@pytest.mark.asyncio
async def test_play_without_auth_is_rejected(auth_server):
    """A client that skips the auth handshake and sends 'play' straight away
    must be rejected — mandatory auth applies before any room assignment.
    """
    port = 8768
    await auth_server(port)

    async with _connect(port) as ws:
        await _send_json(ws, {"type": "play"})
        msg = await _recv_json(ws)
        assert msg["type"] == "error"


@pytest.mark.asyncio
async def test_reconnect_cannot_impersonate_another_user(auth_server):
    """A reconnect frame naming someone else's username must not rebind
    that seat onto the current (different) authenticated connection.
    """
    port = 8769
    await auth_server(port)

    async with _connect(port) as ws1:
        await _play(ws1, "Victim")

        async with _connect(port) as ws2:
            await _play(ws2, "Bystander")
            await _recv_until(ws1, "game_start")

            async with _connect(port) as attacker_ws:
                await _authenticate(attacker_ws, "Attacker")
                await _send_json(attacker_ws, {"type": "reconnect", "username": "Victim"})
                msg = await _recv_json(attacker_ws)
                assert msg["type"] == "error"


@pytest.mark.asyncio
async def test_invalid_credentials_can_be_retried(auth_server):
    """A failed login attempt returns an error but keeps the socket open for
    a retry, up to the server's attempt budget.
    """
    port = 8770
    await auth_server(port)

    async with _connect(port) as ws:
        await _send_json(ws, {"type": "auth", "action": "login", "username": "Ghost", "password": "wrong"})
        err = await _recv_json(ws)
        assert err["type"] == "error"

        # Socket is still open: a subsequent successful register succeeds.
        await _authenticate(ws, "Ghost", action="register")
