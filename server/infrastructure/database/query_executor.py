"""Infrastructure layer — the single gate every SQL statement passes through.

Owns: parameter-binding discipline (values reach SQLite as bound parameters and
never as SQL text), fail-fast validation of statements and bound values,
allowlist resolution for the identifiers that cannot be parameterized, and the
translation of driver failures into a generic, leak-free `Result`.
Must not own: SQL text (callers supply static statements), connection lifecycle
(`Database` opens and closes it), schema knowledge, or business rules.

Two failure modes are deliberately kept apart:

* A caller bug — non-static SQL shape, wrong parameter arity, an unbindable
  value — raises `QueryContractError` immediately. It cannot be triggered by
  user input, so swallowing it would only hide a defect.
* A runtime database failure raises nothing outward: it is logged in full on
  the server and returned as `Result.fail(GENERIC_ERROR_MESSAGE)`. The
  presentation layer forwards `Result.error` verbatim to the client
  (`ws_server._authenticate`), so a driver message must never reach it.
"""

import logging
import re
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import aiosqlite

from core.model.game_state import Result

_LOGGER = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE = "The request could not be completed. Please try again later."

QueryParameters = Union[Sequence[Any], Mapping[str, Any]]

# Everything SQLite can bind natively. Anything else — a datetime, a DTO, an
# object whose __str__ happens to render SQL — is refused at the gate rather
# than left to the driver's adapters, so no value can smuggle in SQL text.
_BINDABLE_TYPES = (type(None), bool, int, float, str, bytes)

_QMARK_PLACEHOLDER = "?"
_NAMED_PLACEHOLDER_PATTERN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ConnectionProvider = Callable[[], aiosqlite.Connection]


class QueryContractError(ValueError):
    """A statement or parameter set violated the executor's contract.

    Raised, never returned: it signals a programming error at the call site,
    and is unreachable from user input alone.
    """


def _strip_literals_and_comments(sql: str) -> str:
    """Blank out string literals, quoted identifiers, and comments.

    Placeholder counting and the single-statement check must not be fooled by a
    `?`, `:name`, or `;` that merely sits inside a literal such as `'a;b'`.
    Removed spans are replaced by spaces so offsets stay meaningful.
    """
    stripped: List[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]
        if char in ("'", '"', "`"):
            index = _skip_quoted_span(sql, index, char, stripped)
        elif sql.startswith("--", index):
            index = _skip_until(sql, index, "\n", stripped)
        elif sql.startswith("/*", index):
            index = _skip_until(sql, index + 2, "*/", stripped)
        else:
            stripped.append(char)
            index += 1

    return "".join(stripped)


def _skip_quoted_span(sql: str, start: int, quote: str, out: List[str]) -> int:
    """Consume a quoted span starting at *start*, honouring doubled-quote escapes."""
    index = start + 1
    out.append(" ")
    while index < len(sql):
        if sql[index] == quote:
            if index + 1 < len(sql) and sql[index + 1] == quote:
                out.append("  ")
                index += 2
                continue
            out.append(" ")
            return index + 1
        out.append(" ")
        index += 1
    raise QueryContractError("Unterminated quoted span in SQL statement")


def _skip_until(sql: str, start: int, terminator: str, out: List[str]) -> int:
    """Consume from *start* through *terminator* (or end of string)."""
    end = sql.find(terminator, start)
    if end == -1:
        out.append(" " * (len(sql) - start))
        return len(sql)
    span_end = end + len(terminator)
    out.append(" " * (span_end - start))
    return span_end


def _validate_statement(sql: str) -> str:
    """Reject anything that is not a single, non-empty statement.

    Stacked statements (`...; DROP TABLE users`) are the payload shape a
    successful injection needs most, so they are refused here even though
    sqlite3 also declines to run them.
    """
    if not isinstance(sql, str):
        raise QueryContractError(f"SQL statement must be a string, got {type(sql).__name__}")
    if not sql.strip():
        raise QueryContractError("SQL statement must not be empty")

    code_only = _strip_literals_and_comments(sql)
    if ";" in code_only.rstrip().rstrip(";"):
        raise QueryContractError("Only one SQL statement may be executed per call")
    return code_only


def _validate_bindable(value: Any, label: str) -> None:
    if not isinstance(value, _BINDABLE_TYPES):
        raise QueryContractError(
            f"Parameter {label} of type {type(value).__name__} is not bindable; "
            "pass a primitive value, not an object"
        )


def _validate_positional(code_only: str, parameters: Sequence[Any]) -> None:
    expected = code_only.count(_QMARK_PLACEHOLDER)
    if expected != len(parameters):
        raise QueryContractError(
            f"Statement declares {expected} '?' placeholders but {len(parameters)} "
            "parameters were supplied"
        )
    for position, value in enumerate(parameters):
        _validate_bindable(value, f"#{position}")


def _validate_named(code_only: str, parameters: Mapping[str, Any]) -> None:
    declared = set(_NAMED_PLACEHOLDER_PATTERN.findall(code_only))
    supplied = set(parameters)
    missing = declared - supplied
    if missing:
        raise QueryContractError(f"Missing values for named placeholders: {sorted(missing)}")
    if code_only.count(_QMARK_PLACEHOLDER):
        raise QueryContractError("Cannot mix '?' and ':name' placeholders in one statement")
    for name, value in parameters.items():
        _validate_bindable(value, f"':{name}'")


def _validate_parameters(code_only: str, parameters: QueryParameters) -> QueryParameters:
    """Fail fast unless every value can only ever be bound, never parsed.

    A bare `str` is rejected on purpose: SQLite would bind it character by
    character, which is always a mistake and looks like a working query until
    the first multi-character value.
    """
    if isinstance(parameters, Mapping):
        _validate_named(code_only, parameters)
        return dict(parameters)
    if isinstance(parameters, (str, bytes)) or not isinstance(parameters, Sequence):
        raise QueryContractError(
            "Parameters must be a sequence or mapping; "
            f"got {type(parameters).__name__}"
        )
    positional = tuple(parameters)
    _validate_positional(code_only, positional)
    return positional


def resolve_identifier(candidate: str, allowed: Iterable[str]) -> Result[str, str]:
    """Resolve a caller-supplied table/column name against an allowlist.

    Identifiers cannot be parameterized, so a dynamic `ORDER BY` or column
    filter is the one place user input would otherwise have to be concatenated.
    Returning a member of *allowed* — the caller's own literal, never the
    caller's input — keeps that path free of user-controlled SQL text.

    Failure is a `Result`, not a raise: an unknown identifier is reachable
    straight from a client frame.
    """
    permitted = {name for name in allowed}
    if candidate in permitted and _IDENTIFIER_PATTERN.match(candidate):
        return Result.ok(candidate)
    _LOGGER.warning("Rejected non-allowlisted SQL identifier: %r", candidate)
    return Result.fail(GENERIC_ERROR_MESSAGE)


class SecureQueryExecutor:
    """Runs parameterized statements and reports failures without leaking detail.

    Holds no connection of its own: it asks *connection_provider* for the live
    one per call, so it can never outlive or reopen a connection `Database`
    owns.
    """

    def __init__(self, connection_provider: ConnectionProvider) -> None:
        self._connection_provider = connection_provider

    async def fetch_one(
        self, sql: str, parameters: QueryParameters = ()
    ) -> Result[Optional[Tuple], str]:
        """Return the first row as a tuple, or `None` when nothing matched."""
        prepared = self._prepare(sql, parameters)
        try:
            async with self._connection_provider().execute(sql, prepared) as cursor:
                row = await cursor.fetchone()
                return Result.ok(tuple(row) if row is not None else None)
        except aiosqlite.Error as exc:
            return self._contain(sql, exc)

    async def fetch_all(
        self, sql: str, parameters: QueryParameters = ()
    ) -> Result[List[Tuple], str]:
        """Return every matching row as a list of tuples."""
        prepared = self._prepare(sql, parameters)
        try:
            async with self._connection_provider().execute(sql, prepared) as cursor:
                rows = await cursor.fetchall()
                return Result.ok([tuple(row) for row in rows])
        except aiosqlite.Error as exc:
            return self._contain(sql, exc)

    async def execute(
        self, sql: str, parameters: QueryParameters = (), *, commit: bool = True
    ) -> Result[Tuple[int, int], str]:
        """Run a write and report `(rowcount, lastrowid)`.

        Pass ``commit=False`` to enlist the statement in a transaction the
        caller commits itself; the rollback on failure still runs here so a
        contained error never leaves a half-applied batch behind.
        """
        prepared = self._prepare(sql, parameters)
        connection = self._connection_provider()
        try:
            cursor = await connection.execute(sql, prepared)
            if commit:
                await connection.commit()
            return Result.ok((cursor.rowcount, cursor.lastrowid))
        except aiosqlite.Error as exc:
            await self._rollback_quietly(connection)
            return self._contain(sql, exc)

    async def insert_returning_id(
        self, sql: str, parameters: QueryParameters = (), *, commit: bool = True
    ) -> Result[Optional[int], str]:
        """Insert one row and report its id, or `None` if a constraint refused it.

        Separates the two outcomes a caller must tell apart — "this username is
        taken" is an answerable business result, while any other driver failure
        stays generic — without the caller ever seeing a driver exception.
        """
        prepared = self._prepare(sql, parameters)
        connection = self._connection_provider()
        try:
            cursor = await connection.execute(sql, prepared)
            if commit:
                await connection.commit()
            return Result.ok(cursor.lastrowid)
        except aiosqlite.IntegrityError as exc:
            await self._rollback_quietly(connection)
            _LOGGER.warning("Insert refused by a constraint [%s]: %s", _summarize(sql), exc)
            return Result.ok(None)
        except aiosqlite.Error as exc:
            await self._rollback_quietly(connection)
            return self._contain(sql, exc)

    async def execute_many(
        self, sql: str, parameter_sets: Sequence[QueryParameters], *, commit: bool = True
    ) -> Result[int, str]:
        """Run one statement over many parameter sets; reports rows affected."""
        prepared = [self._prepare(sql, parameters) for parameters in parameter_sets]
        connection = self._connection_provider()
        try:
            cursor = await connection.executemany(sql, prepared)
            if commit:
                await connection.commit()
            return Result.ok(cursor.rowcount)
        except aiosqlite.Error as exc:
            await self._rollback_quietly(connection)
            return self._contain(sql, exc)

    @staticmethod
    def _prepare(sql: str, parameters: QueryParameters) -> QueryParameters:
        code_only = _validate_statement(sql)
        return _validate_parameters(code_only, parameters)

    @staticmethod
    def _contain(sql: str, exc: aiosqlite.Error) -> Result[Any, str]:
        """Log the real failure server-side and hand back a generic message.

        The bound parameters are deliberately absent from the log line: they
        carry plaintext passwords on the auth path.
        """
        _LOGGER.exception("Query failed [%s]: %s", _summarize(sql), exc)
        return Result.fail(GENERIC_ERROR_MESSAGE)

    @staticmethod
    async def _rollback_quietly(connection: aiosqlite.Connection) -> None:
        try:
            await connection.rollback()
        except aiosqlite.Error as exc:
            _LOGGER.error("Rollback after a failed statement also failed: %s", exc)


def _summarize(sql: str) -> str:
    """One-line, truncated statement text for logs."""
    collapsed = " ".join(sql.split())
    return collapsed if len(collapsed) <= 120 else f"{collapsed[:117]}..."
