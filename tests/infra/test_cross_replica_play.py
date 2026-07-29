"""The exit criteria for Steps 5 and 6, over real infrastructure.

Two `KFChessServer` instances on two ports, sharing one Redis and one NATS —
which is what "two replicas behind one load balancer" means once the balancer
itself is taken out of the picture as uninteresting. Real WebSocket clients
connect to each.

What is proven here cannot be proven with an in-process stand-in:

* **Step 5** — a player who queued on replica A is matched against a player who
  queued on replica B, and the seat is findable from either replica afterwards.
  Two in-process lists can never do this.
* **Step 6** — a move made by the player on one replica is rendered by the
  opponent on the other. The move crosses `room.<id>.commands` inbound and the
  resulting event crosses `room.<id>.events` outbound, neither instance knowing
  where the other's socket is.
"""

import asyncio
import json
import socket
import uuid

import pytest
import pytest_asyncio
import websockets

from core.config import consts
from server.application.gateway_relay import GatewayRelay
from server.application.remote_seat import RemoteSeat
from server.application.room_command_listener import RoomCommandListener
from server.application.room_manager import RoomManager
from server.application.auth_service import AuthService
from server.domain.matchmaking.queue import MatchmakingQueue
from server.infrastructure.broker.nats_broker import NatsBroker
from server.infrastructure.coordination.redis_directory import RedisDirectory
from server.infrastructure.coordination.redis_queue_backend import RedisQueueBackend
from server.infrastructure.database.database import Database
from server.presentation.ws_server import KFChessServer

pytestmark = pytest.mark.infra

_RECV_TIMEOUT = 5.0
_PASSWORD = "password123"
_CLOSE_TIMEOUT = 0.3

# Far longer than any test here takes, so the bot fallback never fires and steals
# a seat from the human opponent the test is waiting for.
_NO_BOT_FALLBACK = 600.0


class Replica:
    """One `ws` instance, with everything Step 5 and 6 give it."""

    def __init__(self, server: KFChessServer, broker: NatsBroker, instance_id: str) -> None:
        self.server = server
        self.broker = broker
        self.instance_id = instance_id

    @property
    def port(self) -> int:
        return self.server.port


async def _build_replica(
    port: int, instance_id: str, db_path: str, redis_connection, nats_url: str
) -> Replica:
    database = Database(db_path)
    await database.connect()

    broker = NatsBroker(url=nats_url)
    await broker.connect()

    directory = RedisDirectory(redis_connection, replica_id=instance_id)
    relay = GatewayRelay(broker)

    def resolve_remote(ticket):
        return RemoteSeat(
            username=ticket.username,
            user_id=ticket.user_id,
            elo=ticket.elo,
            broker=broker,
            replica=ticket.replica,
        )

    matchmaker = MatchmakingQueue(
        backend=RedisQueueBackend(redis_connection),
        replica_id=instance_id,
        timeout_seconds=_NO_BOT_FALLBACK,
        remote_resolver=resolve_remote,
    )
    room_manager = RoomManager(
        database=database,
        seat_directory=directory,
        room_directory=directory,
        instance_id=instance_id,
        broker=broker,
        relay=relay,
    )
    room_manager.attach_command_listener(RoomCommandListener(broker, room_manager))

    server = KFChessServer(
        host="localhost",
        port=port,
        database=database,
        auth_service=AuthService(database),
        matchmaker=matchmaker,
        room_manager=room_manager,
        relay=relay,
    )
    await server.start()
    return Replica(server, broker, instance_id)


def _free_port() -> int:
    """Claim a port the OS says is free, then release it for the server to bind.

    Fixed ports are wrong here for two reasons that both bite: consecutive tests
    can race a listener that has not finished releasing, and a leftover process
    from an interrupted run makes every subsequent run fail at setup with an
    error that looks like a product fault rather than stale state.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest_asyncio.fixture
async def replicas(redis_connection, nats_url, tmp_path):
    """Two replicas sharing one Redis and one NATS, on one SQLite file.

    They share the database because user accounts have to resolve identically on
    both — which in a real deployment is PostgreSQL, and here is one file two
    processes-in-one open. What is genuinely shared and genuinely under test is
    the queue, the directory and the broker.
    """
    db_path = str(tmp_path / "fleet.db")
    first = await _build_replica(_free_port(), "replica-a", db_path, redis_connection, nats_url)
    second = await _build_replica(_free_port(), "replica-b", db_path, redis_connection, nats_url)
    yield first, second
    for replica in (first, second):
        await replica.server.stop()
        await replica.broker.close()


def _connect(port: int):
    return websockets.connect(f"ws://localhost:{port}", close_timeout=_CLOSE_TIMEOUT)


async def _send(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload))


async def _recv(ws, timeout: float = _RECV_TIMEOUT) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def _authenticate(ws, username: str) -> None:
    await _send(ws, {"type": "auth", "action": "register", "username": username, "password": _PASSWORD})
    reply = await _recv(ws)
    assert reply["type"] == "auth" and reply.get("status") == "ok", reply


async def _recv_until(ws, msg_type: str, timeout: float = _RECV_TIMEOUT) -> dict:
    """Receive until *msg_type* arrives, answering pings and skipping the rest."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"Timed out waiting for {msg_type!r}")
        message = await _recv(ws, timeout=remaining)
        if message.get("type") == "ping":
            await _send(ws, {"type": "pong"})
            continue
        if message.get("type") == msg_type:
            return message


def _unique(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_a_player_on_one_replica_is_matched_against_a_player_on_the_other(replicas):
    """Step 5's exit criterion, end to end over two sockets on two ports."""
    first, second = replicas
    alice, bob = _unique("Alice"), _unique("Bob")

    async with _connect(first.port) as ws_a, _connect(second.port) as ws_b:
        await _authenticate(ws_a, alice)
        await _authenticate(ws_b, bob)

        await _send(ws_a, {"type": "play"})
        # A moment for A's ticket to land in Redis, so the pairing is genuinely
        # "B finds A's ticket" rather than a race that could resolve either way.
        await asyncio.sleep(0.3)
        await _send(ws_b, {"type": "play"})

        start_a = await _recv_until(ws_a, "game_start")
        start_b = await _recv_until(ws_b, "game_start")

    assert start_a["room_id"] == start_b["room_id"], "the two players must share one room"
    assert start_a["opponent"] == bob
    assert start_b["opponent"] == alice
    assert {start_a["color"], start_b["color"]} == {consts.COLOR_WHITE, consts.COLOR_BLACK}


@pytest.mark.asyncio
async def test_the_seat_is_findable_from_either_replica(replicas):
    """What makes a reconnect land anywhere: the directory answers the same
    question from both instances, including the one that owns nothing."""
    first, second = replicas
    alice, bob = _unique("Alice"), _unique("Bob")

    async with _connect(first.port) as ws_a, _connect(second.port) as ws_b:
        await _authenticate(ws_a, alice)
        await _authenticate(ws_b, bob)
        await _send(ws_a, {"type": "play"})
        await asyncio.sleep(0.3)
        await _send(ws_b, {"type": "play"})
        room_id = (await _recv_until(ws_a, "game_start"))["room_id"]
        await _recv_until(ws_b, "game_start")

        await first.server.room_manager.drain_directory_writes()
        await second.server.room_manager.drain_directory_writes()

        from_a = await first.server.room_manager.locate_seat(alice)
        from_b = await second.server.room_manager.locate_seat(alice)

    assert from_a is not None and from_b is not None
    assert from_a.room_id == from_b.room_id == room_id
    assert from_a.replica == from_b.replica, "both replicas agree on where the seat is"


@pytest.mark.asyncio
async def test_a_move_on_one_replica_is_rendered_by_the_opponent_on_the_other(replicas):
    """Step 6's exit criterion.

    Whoever holds White moves; the opponent, connected to the *other* instance,
    receives the resulting `event_move_started` carrying the squares and the
    arrival time it interpolates from. The move travels inbound over
    `room.<id>.commands` and the event outbound over `room.<id>.events`.
    """
    first, second = replicas
    alice, bob = _unique("Alice"), _unique("Bob")

    async with _connect(first.port) as ws_a, _connect(second.port) as ws_b:
        await _authenticate(ws_a, alice)
        await _authenticate(ws_b, bob)
        await _send(ws_a, {"type": "play"})
        await asyncio.sleep(0.3)
        await _send(ws_b, {"type": "play"})

        start_a = await _recv_until(ws_a, "game_start")
        start_b = await _recv_until(ws_b, "game_start")

        white_ws, black_ws = (
            (ws_a, ws_b) if start_a["color"] == consts.COLOR_WHITE else (ws_b, ws_a)
        )

        await _send(
            white_ws,
            {"type": "move", "from": "e2", "to": "e4", "move_id": uuid.uuid4().hex},
        )

        seen_by_opponent = await _recv_until(black_ws, "event_move_started")

    assert seen_by_opponent["from"] == "e2"
    assert seen_by_opponent["to"] == "e4"
    assert seen_by_opponent["color"] == consts.COLOR_WHITE
    assert seen_by_opponent["arrival_ms"] > seen_by_opponent["at_ms"], (
        "the event must carry the travel window the client interpolates over"
    )


@pytest.mark.asyncio
async def test_the_mover_also_sees_their_own_move(replicas):
    """The event stream is the update for every recipient, including the one who
    caused it — there is no separate acknowledgement path that could disagree."""
    first, second = replicas
    alice, bob = _unique("Alice"), _unique("Bob")

    async with _connect(first.port) as ws_a, _connect(second.port) as ws_b:
        await _authenticate(ws_a, alice)
        await _authenticate(ws_b, bob)
        await _send(ws_a, {"type": "play"})
        await asyncio.sleep(0.3)
        await _send(ws_b, {"type": "play"})

        start_a = await _recv_until(ws_a, "game_start")
        await _recv_until(ws_b, "game_start")
        white_ws = ws_a if start_a["color"] == consts.COLOR_WHITE else ws_b

        await _send(
            white_ws, {"type": "move", "from": "d2", "to": "d4", "move_id": uuid.uuid4().hex}
        )
        seen_by_mover = await _recv_until(white_ws, "event_move_started")

    assert (seen_by_mover["from"], seen_by_mover["to"]) == ("d2", "d4")
