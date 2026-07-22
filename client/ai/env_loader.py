"""Reads client-side integration secrets from the environment or a .env file.

Owns: locating the repo-root .env, parsing its KEY=VALUE lines, and generic
name→value lookup. Must not own: HTTP, prompts, game logic, or knowledge of
any specific provider's variable names — those belong to the provider registry
(providers.py); this module is pure configuration lookup.

The process environment always wins over the .env file, so a key exported in
the shell behaves exactly like a key written to .env.
"""

import os
from pathlib import Path
from typing import Dict, Optional

from shared.config.consts import FILE_ENCODING

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = _REPO_ROOT / ".env"

# .env line syntax.
_COMMENT_PREFIX = "#"
_KEY_VALUE_SEPARATOR = "="
_VALUE_QUOTE_CHARS = "'\""


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse *path* as KEY=VALUE lines.

    Comments, blank lines, and lines without ``=`` are skipped; surrounding
    quotes on a value are stripped. A missing file reads as no values, so an
    unconfigured checkout behaves the same as an empty .env.
    """
    if not path.is_file():
        return {}
    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding=FILE_ENCODING).splitlines():
        line = raw_line.strip()
        if not line or line.startswith(_COMMENT_PREFIX) or _KEY_VALUE_SEPARATOR not in line:
            continue
        key, _, value = line.partition(_KEY_VALUE_SEPARATOR)
        values[key.strip()] = value.strip().strip(_VALUE_QUOTE_CHARS)
    return values


def read_env_value(name: str, env_file: Path = DEFAULT_ENV_FILE) -> Optional[str]:
    """*name* from the process environment, else from *env_file*; None if absent or empty."""
    value = os.environ.get(name) or parse_env_file(env_file).get(name)
    return value or None
