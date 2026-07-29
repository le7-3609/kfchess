"""Infrastructure layer — the parts of the query gate that no driver owns.

Owns: the contract every SQL statement must satisfy regardless of which database
runs it — single statement only, no unbindable values, allowlisted identifiers —
and the generic message a contained failure is reported as.
Must not own: placeholder syntax (that is per-dialect), connection lifecycle, or
SQL text.

Extracted from `query_executor.py` when the server gained a second driver. The
contract is the security property; `?` versus `$n` is a detail of the wire
protocol underneath it, and the two must not be able to drift apart — porting to
PostgreSQL is exactly the moment a re-typed validation rule would silently lose a
check.

Two failure modes are deliberately kept apart:

* A caller bug — non-static SQL shape, wrong parameter arity, an unbindable
  value — raises `QueryContractError` immediately. It cannot be triggered by
  user input, so swallowing it would only hide a defect.
* A runtime database failure raises nothing outward: it is logged in full on
  the server and returned as `Result.fail(GENERIC_ERROR_MESSAGE)`. The
  presentation layer forwards `Result.error` verbatim to the client, so a driver
  message must never reach it.
"""

import logging
import re
from typing import Any, Iterable, List

from core.model.game_state import Result

_LOGGER = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE = "The request could not be completed. Please try again later."

# Everything a driver can bind natively. Anything else — a datetime, a DTO, an
# object whose __str__ happens to render SQL — is refused at the gate rather
# than left to the driver's adapters, so no value can smuggle in SQL text.
#
# Timestamps are passed as ISO strings and cast in the statement rather than
# added here: widening this tuple to admit `datetime` would also admit every
# other object a driver happens to know how to adapt, which is the check.
BINDABLE_TYPES = (type(None), bool, int, float, str, bytes)

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class QueryContractError(ValueError):
    """A statement or parameter set violated the executor's contract.

    Raised, never returned: it signals a programming error at the call site,
    and is unreachable from user input alone.
    """


def strip_literals_and_comments(sql: str) -> str:
    """Blank out string literals, quoted identifiers, and comments.

    Placeholder counting and the single-statement check must not be fooled by a
    `?`, `$1`, `:name`, or `;` that merely sits inside a literal such as `'a;b'`.
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


def validate_statement(sql: str) -> str:
    """Reject anything that is not a single, non-empty statement.

    Stacked statements (`...; DROP TABLE users`) are the payload shape a
    successful injection needs most, so they are refused here even though both
    drivers also decline to run them.
    """
    if not isinstance(sql, str):
        raise QueryContractError(f"SQL statement must be a string, got {type(sql).__name__}")
    if not sql.strip():
        raise QueryContractError("SQL statement must not be empty")

    code_only = strip_literals_and_comments(sql)
    if ";" in code_only.rstrip().rstrip(";"):
        raise QueryContractError("Only one SQL statement may be executed per call")
    return code_only


def validate_bindable(value: Any, label: str) -> None:
    if not isinstance(value, BINDABLE_TYPES):
        raise QueryContractError(
            f"Parameter {label} of type {type(value).__name__} is not bindable; "
            "pass a primitive value, not an object"
        )


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
    if candidate in permitted and IDENTIFIER_PATTERN.match(candidate):
        return Result.ok(candidate)
    _LOGGER.warning("Rejected non-allowlisted SQL identifier: %r", candidate)
    return Result.fail(GENERIC_ERROR_MESSAGE)


def summarize(sql: str) -> str:
    """One-line, truncated statement text for logs."""
    collapsed = " ".join(sql.split())
    return collapsed if len(collapsed) <= 120 else f"{collapsed[:117]}..."
