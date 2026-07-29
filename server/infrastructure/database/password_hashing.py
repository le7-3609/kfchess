"""Infrastructure layer — the one place a password is hashed or verified.

Owns: the pinned bcrypt cost factor, the off-the-event-loop execution of both
hashing and verification, and reading the cost back out of a stored hash so it
can be upgraded on login.
Must not own: which user a hash belongs to, when to rehash (the database adapter
decides, on a successful login), or storage.

Extracted from `database.py` when a second driver appeared. The cost factor is
the work protecting every password in the database, and two adapters each
carrying their own copy of it is exactly how one of them ends up a factor
behind.
"""

import asyncio
import logging
import re

import bcrypt

from core.config.consts import FILE_ENCODING

_LOGGER = logging.getLogger(__name__)

# Pinned rather than left to `bcrypt.gensalt()`'s library default, which has
# moved between releases: the work factor protecting every password in this
# database must be a deliberate, reviewable number, not a property of whichever
# bcrypt version the image happened to resolve.
BCRYPT_COST_FACTOR = 12

# A bcrypt hash names its own cost in the third `$`-delimited field
# ("$2b$12$..."), which is what makes rehash-on-login possible without storing
# the cost separately.
_BCRYPT_COST_PATTERN = re.compile(r"^\$2[aby]?\$(\d{2})\$")


async def hash_password(password_plain: str) -> str:
    """Hash a password on a worker thread.

    bcrypt is synchronous CPU work by design, and at cost 12 it occupies a core
    for on the order of a quarter of a second. Running it inline would block the
    same event loop that drives every room's AsyncGameRunner — five missed ticks
    in every game on this process, per login. Off-loading it is a correctness fix
    for the simulation, not only a throughput fix for auth.
    """
    return await asyncio.to_thread(_hash_password_blocking, password_plain)


async def verify_password(password_plain: str, stored_hash: str) -> bool:
    """Check a password on a worker thread, for the same reason as hashing."""
    return await asyncio.to_thread(_verify_password_blocking, password_plain, stored_hash)


def cost_of(stored_hash: str) -> int:
    """The work factor encoded in a bcrypt hash, or 0 if unreadable.

    An unreadable hash reports 0 so it sorts below any target and gets rehashed
    on the next successful login.
    """
    match = _BCRYPT_COST_PATTERN.match(stored_hash or "")
    return int(match.group(1)) if match else 0


def needs_rehash(stored_hash: str) -> bool:
    """Whether *stored_hash* predates the current cost factor."""
    return cost_of(stored_hash) < BCRYPT_COST_FACTOR


def _hash_password_blocking(password_plain: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_COST_FACTOR)
    return bcrypt.hashpw(password_plain.encode(FILE_ENCODING), salt).decode(FILE_ENCODING)


def _verify_password_blocking(password_plain: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password_plain.encode(FILE_ENCODING), stored_hash.encode(FILE_ENCODING)
        )
    except ValueError:
        # A stored value that is not a bcrypt hash at all cannot match any
        # password; it must read as a failed login rather than an exception that
        # escapes into the auth path.
        _LOGGER.error("Stored credential is not a valid bcrypt hash")
        return False
