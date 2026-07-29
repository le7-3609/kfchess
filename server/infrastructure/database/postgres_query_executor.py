"""Infrastructure layer — the PostgreSQL gate every SQL statement passes through.

Owns: PostgreSQL's placeholder discipline (`$n`, positional only), and the
translation of `asyncpg` failures into a generic, leak-free `Result`.
Must not own: the contract itself (`query_contract.py`), SQL text, connection
lifecycle (`PostgresDatabase` owns the pool), or business rules.

Three differences from the SQLite executor, and nothing else:

* **`$n` instead of `?`.** Arity is checked against the highest-numbered
  placeholder rather than a count of `?` characters, because `$1` may legally
  appear twice in one statement while still binding one value — counting
  occurrences would reject a correct statement.
* **No `:name` form.** asyncpg has no named parameters, so a mapping is refused
  outright rather than silently reinterpreted.
* **A pool, not a connection.** Each call acquires and releases, so a slow query
  holds one connection instead of blocking every other caller behind a single
  shared one. The `commit=False` transaction path therefore takes an explicit
  connection from the caller, since "the current transaction" is not a property
  of a pool.

The contract that matters for security is unchanged, and deliberately shared
rather than re-typed: same single-statement check, same bindable-value refusal,
same allowlisted identifiers, same generic error text.
"""

import logging
import re
from typing import Any, Callable, List, Optional, Sequence, Tuple

try:
    import asyncpg
except ImportError:  # pragma: no cover - exercised only where asyncpg is absent
    asyncpg = None  # type: ignore[assignment]

from core.model.game_state import Result
from server.infrastructure.database.query_contract import (
    GENERIC_ERROR_MESSAGE,
    QueryContractError,
    summarize,
    validate_bindable,
    validate_statement,
)

_LOGGER = logging.getLogger(__name__)

# `$12` and friends, so arity is derived from the highest index rather than from
# how many times each placeholder happens to be written.
_NUMBERED_PLACEHOLDER_PATTERN = re.compile(r"\$(\d+)")

PoolProvider = Callable[[], Any]


def postgres_available() -> bool:
    """Whether the asyncpg driver is importable in this process."""
    return asyncpg is not None


def _driver_errors() -> tuple:
    """The exception types a contained failure may be, driver-absent included.

    Returned as a tuple rather than referenced directly so `except` clauses stay
    valid in a process where asyncpg was never installed — the SQLite path must
    keep working there.
    """
    if asyncpg is None:
        return (OSError,)
    return (asyncpg.PostgresError, OSError)


def _integrity_errors() -> tuple:
    """Constraint violations, which are an answerable business result."""
    if asyncpg is None:
        return ()
    return (asyncpg.IntegrityConstraintViolationError,)


def validate_numbered_parameters(code_only: str, parameters: Any) -> Tuple:
    """Fail fast unless every value can only ever be bound, never parsed.

    A bare `str` is rejected for the same reason the SQLite gate rejects it: a
    caller who meant one value and passed a string will otherwise get a
    confusing arity error at best, and a wrong query at worst.
    """
    if isinstance(parameters, (str, bytes)) or not isinstance(parameters, Sequence):
        raise QueryContractError(
            "Parameters must be a sequence of positional values; "
            f"got {type(parameters).__name__}"
        )

    indexes = [int(match) for match in _NUMBERED_PLACEHOLDER_PATTERN.findall(code_only)]
    expected = max(indexes) if indexes else 0
    positional = tuple(parameters)
    if expected != len(positional):
        raise QueryContractError(
            f"Statement declares {expected} '$n' placeholders but {len(positional)} "
            "parameters were supplied"
        )
    if indexes and sorted(set(indexes)) != list(range(1, expected + 1)):
        raise QueryContractError(
            f"Statement's '$n' placeholders are not a contiguous 1..{expected} range"
        )
    for position, value in enumerate(positional, start=1):
        validate_bindable(value, f"${position}")
    return positional


class PostgresQueryExecutor:
    """Runs parameterized statements against a pool, leaking no driver detail."""

    def __init__(self, pool_provider: PoolProvider) -> None:
        self._pool_provider = pool_provider

    async def fetch_one(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> Result[Optional[Tuple], str]:
        """Return the first row as a tuple, or `None` when nothing matched."""
        prepared = self._prepare(sql, parameters)
        try:
            async with self._pool_provider().acquire() as connection:
                row = await connection.fetchrow(sql, *prepared)
                return Result.ok(tuple(row) if row is not None else None)
        except _driver_errors() as exc:
            return self._contain(sql, exc)

    async def fetch_all(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> Result[List[Tuple], str]:
        """Return every matching row as a list of tuples."""
        prepared = self._prepare(sql, parameters)
        try:
            async with self._pool_provider().acquire() as connection:
                rows = await connection.fetch(sql, *prepared)
                return Result.ok([tuple(row) for row in rows])
        except _driver_errors() as exc:
            return self._contain(sql, exc)

    async def execute(
        self, sql: str, parameters: Sequence[Any] = (), *, connection: Any = None
    ) -> Result[Tuple[int, int], str]:
        """Run a write and report `(rowcount, 0)`.

        The second element is always 0: PostgreSQL has no `lastrowid`, and an
        inserted id must be asked for with `RETURNING`. Reporting a fabricated
        one would be worse than reporting none, so callers who need the id use
        `insert_returning_id`.

        Pass *connection* to enlist the statement in a transaction the caller is
        already inside; without one, the statement is its own transaction.
        """
        prepared = self._prepare(sql, parameters)
        try:
            async with self._connection(connection) as conn:
                status = await conn.execute(sql, *prepared)
                return Result.ok((_rows_affected(status), 0))
        except _driver_errors() as exc:
            return self._contain(sql, exc)

    async def insert_returning_id(
        self, sql: str, parameters: Sequence[Any] = (), *, connection: Any = None
    ) -> Result[Optional[int], str]:
        """Insert one row and report its id, or `None` if a constraint refused it.

        The statement must carry its own `RETURNING id`; unlike SQLite there is
        no out-of-band way to learn it. A `DO NOTHING` conflict returns no row,
        which is reported as `None` — the same answer a constraint violation
        gives, because to the caller both mean "the row you asked for is not the
        one that is there".
        """
        prepared = self._prepare(sql, parameters)
        try:
            async with self._connection(connection) as conn:
                row = await conn.fetchrow(sql, *prepared)
                return Result.ok(row[0] if row is not None else None)
        except _integrity_errors() as exc:
            _LOGGER.warning("Insert refused by a constraint [%s]: %s", summarize(sql), exc)
            return Result.ok(None)
        except _driver_errors() as exc:
            return self._contain(sql, exc)

    async def execute_many(
        self, sql: str, parameter_sets: Sequence[Sequence[Any]], *, connection: Any = None
    ) -> Result[int, str]:
        """Run one statement over many parameter sets; reports rows submitted.

        `executemany` reports no per-statement count, so the answer is the
        number of parameter sets accepted — which is the number of rows written,
        since the whole batch is one transaction that either lands or raises.
        """
        prepared = [self._prepare(sql, parameters) for parameters in parameter_sets]
        if not prepared:
            return Result.ok(0)
        try:
            async with self._connection(connection) as conn:
                await conn.executemany(sql, prepared)
                return Result.ok(len(prepared))
        except _driver_errors() as exc:
            return self._contain(sql, exc)

    def _connection(self, connection: Any):
        """Either the caller's connection, or one borrowed from the pool.

        Wrapping a caller-supplied connection in a no-op context manager keeps
        every method above written one way, rather than each branching on
        whether it owns the connection it is using.
        """
        if connection is not None:
            return _BorrowedConnection(connection)
        return self._pool_provider().acquire()

    @staticmethod
    def _prepare(sql: str, parameters: Sequence[Any]) -> Tuple:
        code_only = validate_statement(sql)
        return validate_numbered_parameters(code_only, parameters)

    @staticmethod
    def _contain(sql: str, exc: Exception) -> Result[Any, str]:
        """Log the real failure server-side and hand back a generic message.

        The bound parameters are deliberately absent from the log line: they
        carry plaintext passwords on the auth path.
        """
        _LOGGER.exception("Query failed [%s]: %s", summarize(sql), exc)
        return Result.fail(GENERIC_ERROR_MESSAGE)


class _BorrowedConnection:
    """Presents an already-acquired connection as an async context manager.

    Deliberately does not close it: the caller opened the transaction and the
    caller ends it.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def __aenter__(self) -> Any:
        return self._connection

    async def __aexit__(self, *_exc_info) -> None:
        return None


def _rows_affected(status: str) -> int:
    """Parse asyncpg's command tag ("UPDATE 3") into a row count.

    Returns 0 for a tag with no trailing number, which is what every
    non-row-affecting command reports.
    """
    tail = (status or "").rsplit(" ", 1)[-1]
    return int(tail) if tail.isdigit() else 0
