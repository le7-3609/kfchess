"""Unit tests for structured logging.

A container's filesystem dies with the container, so logs are read in a central
store. Correlating one game across the processes that handled it only works if
`room_id` and `user_id` are fields — which is what these tests pin.
"""

import asyncio
import io
import json
import logging

import pytest

from server.infrastructure.logging.json_logging import (
    JsonLogFormatter,
    bind_room,
    bind_session,
    configure_json_logging,
    room_context,
)


def _emit(message: str = "hello", **kwargs) -> dict:
    """Emit one record through the JSON formatter and read it back."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger(f"test.{id(stream)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    logger.info(message, **kwargs)
    return json.loads(stream.getvalue())


def test_a_record_renders_as_one_json_object():
    entry = _emit("room opened")

    assert entry["message"] == "room opened"
    assert entry["level"] == "INFO"
    assert "ts" in entry and "logger" in entry


def test_message_arguments_are_interpolated_not_left_as_a_template():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("test.args")
    logger.handlers = [handler]
    logger.propagate = False

    logger.warning("Room %s expired after %d ticks", "AB12CD", 7)

    assert json.loads(stream.getvalue())["message"] == "Room AB12CD expired after 7 ticks"


def test_room_context_is_attached_as_a_field(cleanup_context):
    with room_context("AB12CD"):
        entry = _emit()

    assert entry["room_id"] == "AB12CD"


def test_room_context_does_not_leak_past_its_block(cleanup_context):
    with room_context("AB12CD"):
        pass

    assert "room_id" not in _emit()


def test_session_identity_is_attached_as_fields(cleanup_context):
    bind_session(42, "Alice")

    entry = _emit()

    assert entry["user_id"] == 42
    assert entry["username"] == "Alice"


def test_unset_context_fields_are_omitted_rather_than_null(cleanup_context):
    """A null `room_id` on every line from the HTTP tier is noise in a log
    store that indexes by field."""
    entry = _emit()

    assert "room_id" not in entry
    assert "user_id" not in entry


@pytest.mark.asyncio
async def test_context_is_per_task_so_rooms_cannot_bleed_into_each_other(cleanup_context):
    """Two rooms tick as two tasks on one loop; a context variable that leaked
    across them would mislabel every line."""
    seen = {}

    async def room_task(room_id: str) -> None:
        bind_room(room_id)
        await asyncio.sleep(0)
        seen[room_id] = _emit().get("room_id")

    await asyncio.gather(room_task("ROOM_A"), room_task("ROOM_B"))

    assert seen == {"ROOM_A": "ROOM_A", "ROOM_B": "ROOM_B"}


def test_extra_fields_are_emitted_as_their_own_keys():
    entry = _emit("scaled", extra={"replica_count": 4})

    assert entry["replica_count"] == 4


def test_an_exception_is_recorded_as_a_field():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("test.exception")
    logger.handlers = [handler]
    logger.propagate = False

    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("save failed")

    entry = json.loads(stream.getvalue())
    assert "ValueError: boom" in entry["exception"]


def test_configure_replaces_handlers_rather_than_adding_to_them():
    """A second call — a test, or an entry point invoked twice — must not
    duplicate every line."""
    stream = io.StringIO()
    configure_json_logging("INFO", stream)
    configure_json_logging("INFO", stream)

    logging.getLogger("test.configure").info("once")

    assert len(stream.getvalue().strip().splitlines()) == 1


@pytest.fixture
def cleanup_context():
    """Reset the context variables so one test cannot colour another's records."""
    yield
    bind_session(None, None)
    bind_room(None)
