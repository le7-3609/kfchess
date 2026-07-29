"""Infrastructure layer — the SQLite gate every SQL statement passes through.

Owns: SQLite's placeholder discipline (`?` and `:name`), and the translation of
`aiosqlite` failures into a generic, leak-free `Result`.
Must not own: the contract itself (`query_contract.py` holds the parts no driver
owns), SQL text (callers supply static statements), connection lifecycle
(`Database` opens and closes it), schema knowledge, or business rules.

The dialect-independent rules — single statement only, unbindable values
refused, identifiers allowlisted, driver detail contained — live in
`query_contract` and are shared verbatim with `PostgresQueryExecutor`, so the
two drivers cannot drift into enforcing different things.
"""

import logging
import re
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple, Union

import aiosqlite

from core.model.game_state import Result
from server.infrastructure.database.query_contract import (
    GENERIC_ERROR_MESSAGE,
    QueryContractError,
    resolve_identifier,
    summarize,
    validate_bindable,
    validate_statement,
)

_LOGGER = logging.getLogger(__name__)

QueryParameters = Union[Sequence[Any], Mapping[str, Any]]

_QMARK_PLACEHOLDER = "?"
_NAMED_PLACEHOLDER_PATTERN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")

ConnectionProvider = Callable[[], aiosqlite.Connection]

# Re-exported so callers keep importing the contract from the executor they use.
__all__ = [
    "GENERIC_ERROR_MESSAGE",
    "QueryContractError",
    "QueryParameters",
    "SecureQueryExecutor",
    "resolve_identifier",
]


def _validate_positional(code_only: str, parameters: Sequence[Any]) -> None:
    expected = code_only.count(_QMARK_PLACEHOLDER)
    if expected != len(parameters):
        raise QueryContractError(
            f"Statement declares {expected} '?' placeholders but {len(parameters)} "
            "parameters were supplied"
        )
    for position, value in enumerate(parameters):
        validate_bindable(value, f"#{position}")


def _validate_named(code_only: str, parameters: Mapping[str, Any]) -> None:
    declared = set(_NAMED_PLACEHOLDER_PATTERN.findall(code_only))
    supplied = set(parameters)
    missing = declared - supplied
    if missing:
        raise QueryContractError(f"Missing values for named placeholders: {sorted(missing)}")
    if code_only.count(_QMARK_PLACEHOLDER):
        raise QueryContractError("Cannot mix '?' and ':name' placeholders in one statement")
    for name, value in parameters.items():
        validate_bindable(value, f"':{name}'")


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
            _LOGGER.warning("Insert refused by a constraint [%s]: %s", summarize(sql), exc)
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
        code_only = validate_statement(sql)
        return _validate_parameters(code_only, parameters)

    @staticmethod
    def _contain(sql: str, exc: aiosqlite.Error) -> Result[Any, str]:
        """Log the real failure server-side and hand back a generic message.

        The bound parameters are deliberately absent from the log line: they
        carry plaintext passwords on the auth path.
        """
        _LOGGER.exception("Query failed [%s]: %s", summarize(sql), exc)
        return Result.fail(GENERIC_ERROR_MESSAGE)

    @staticmethod
    async def _rollback_quietly(connection: aiosqlite.Connection) -> None:
        try:
            await connection.rollback()
        except aiosqlite.Error as exc:
            _LOGGER.error("Rollback after a failed statement also failed: %s", exc)
