"""Structured logging — JSON records carrying the ids that correlate a game.

Owns: the JSON log formatter, the context variables that stamp `room_id` and
`user_id` onto every record emitted below a connection or a room, and the
root-logger configuration the entry points call once at startup.
Must not own: what is logged or at which level — callers own that.

A container's filesystem dies with the container, so logs are shipped to a
central store and read there. Correlating one game across the processes that
handled it is only possible if the identifiers are *fields* rather than prose
inside a message, which is the whole reason this replaces the human-readable
formatter the entry points used to configure.
"""

import contextvars
import datetime as _datetime
import json
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

FIELD_TIMESTAMP = "ts"
FIELD_LEVEL = "level"
FIELD_LOGGER = "logger"
FIELD_MESSAGE = "message"
FIELD_ROOM_ID = "room_id"
FIELD_USER_ID = "user_id"
FIELD_USERNAME = "username"
FIELD_EXCEPTION = "exception"

# Attributes LogRecord always carries. Anything on a record that is *not* here
# was attached by a caller through `extra=`, so it is emitted as its own field
# rather than being lost inside the formatted message.
_STANDARD_RECORD_ATTRIBUTES = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "taskName", "thread", "threadName",
    }
)

_current_room_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "kfchess_room_id", default=None
)
_current_user_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "kfchess_user_id", default=None
)
_current_username: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "kfchess_username", default=None
)


@contextmanager
def room_context(room_id: str) -> Iterator[None]:
    """Stamp *room_id* onto every record emitted inside this block."""
    token = _current_room_id.set(room_id)
    try:
        yield
    finally:
        _current_room_id.reset(token)


def bind_room(room_id: str) -> None:
    """Stamp *room_id* onto this task's records for the rest of its life.

    The unscoped form exists for a room's own background tasks — a tick loop
    owns its context for as long as it runs, so there is no block to leave.
    Context variables are per-task, so this cannot leak into a sibling room.
    """
    _current_room_id.set(room_id)


def bind_session(user_id: Optional[int], username: Optional[str]) -> None:
    """Stamp the authenticated identity onto this connection's records."""
    _current_user_id.set(user_id)
    _current_username.set(username)


def current_context() -> Dict[str, Any]:
    """The correlation fields currently in scope, omitting the unset ones."""
    context: Dict[str, Any] = {}
    room_id = _current_room_id.get()
    user_id = _current_user_id.get()
    username = _current_username.get()
    if room_id is not None:
        context[FIELD_ROOM_ID] = room_id
    if user_id is not None:
        context[FIELD_USER_ID] = user_id
    if username is not None:
        context[FIELD_USERNAME] = username
    return context


class JsonLogFormatter(logging.Formatter):
    """Renders a LogRecord as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            FIELD_TIMESTAMP: _datetime.datetime.fromtimestamp(
                record.created, tz=_datetime.timezone.utc
            ).isoformat(),
            FIELD_LEVEL: record.levelname,
            FIELD_LOGGER: record.name,
            FIELD_MESSAGE: record.getMessage(),
        }
        payload.update(current_context())
        payload.update(self._extra_fields(record))

        if record.exc_info:
            payload[FIELD_EXCEPTION] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)

    @staticmethod
    def _extra_fields(record: logging.LogRecord) -> Dict[str, Any]:
        return {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_ATTRIBUTES and not key.startswith("_")
        }


def configure_json_logging(level: str = "INFO", stream: Any = None) -> None:
    """Install the JSON formatter on the root logger, replacing any handlers.

    Handlers are replaced rather than added so a second call (a test, or an
    entry point invoked twice in one process) cannot duplicate every line.
    """
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
