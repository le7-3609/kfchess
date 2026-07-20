"""Enforces the server's layer dependency arrows.

The server is split into four layers whose dependencies point one way:

    presentation -> application -> domain <- infrastructure

Every other refactor in the suite is checked by exercising behaviour, but a
layering violation breaks nothing at runtime — it only rots the design until
someone notices. So it is asserted structurally here, by reading the imports
each module actually declares.

Modules left at the top of `server/` are compatibility shims re-exporting the
moved implementations; they are exempt, since re-exporting from every layer is
the whole reason they exist.
"""

import ast
import pathlib
from typing import Dict, List, Set

import pytest

LAYERS = ("domain", "application", "presentation", "infrastructure")

# Who each layer is allowed to import from. A layer may always import itself.
ALLOWED_IMPORTS: Dict[str, Set[str]] = {
    "domain": {"domain"},
    "application": {"application", "domain", "infrastructure"},
    "presentation": {"presentation", "application", "domain", "infrastructure"},
    "infrastructure": {"infrastructure", "domain"},
}

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[2] / "server"


def _layer_of_path(path: pathlib.Path) -> str:
    """Classify a module by the layer package it sits in, or 'shim' if top-level."""
    parts = path.relative_to(SERVER_ROOT).parts
    if parts and parts[0] in LAYERS:
        return parts[0]
    return "shim"


def _layer_of_module(module: str) -> str:
    """Classify an imported `server.*` module name by its layer package."""
    parts = module.split(".")
    if len(parts) > 1 and parts[1] in LAYERS:
        return parts[1]
    return "shim"


def _imported_server_modules(source: str) -> List[tuple]:
    """Yield (module_name, lineno) for every `server.*` import in *source*."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        found.extend((name, node.lineno) for name in names if name.startswith("server."))
    return found


def _layered_modules() -> List[pathlib.Path]:
    return [p for p in sorted(SERVER_ROOT.rglob("*.py")) if _layer_of_path(p) != "shim"]


def test_every_layer_only_imports_layers_it_is_allowed_to():
    violations = []
    for path in _layered_modules():
        source_layer = _layer_of_path(path)
        for module, lineno in _imported_server_modules(path.read_text(encoding="utf-8")):
            target_layer = _layer_of_module(module)
            if target_layer not in ALLOWED_IMPORTS[source_layer]:
                violations.append(
                    f"{path.name}:{lineno} [{source_layer}] imports [{target_layer}] ({module})"
                )

    assert not violations, "layer dependency violations:\n" + "\n".join(violations)


def test_no_layered_module_reaches_through_a_compatibility_shim():
    """Shims exist for external callers; inside the layers, import the real module.

    Routing through a shim hides which layer a dependency actually lands in,
    which is exactly what the arrows above are meant to make visible.
    """
    violations = []
    for path in _layered_modules():
        for module, lineno in _imported_server_modules(path.read_text(encoding="utf-8")):
            if _layer_of_module(module) == "shim":
                violations.append(f"{path.name}:{lineno} imports shim {module}")

    assert not violations, "layered modules must not import shims:\n" + "\n".join(violations)


def test_domain_never_imports_the_outer_world():
    """The domain must stay pure: no sockets, no database driver, no wire encoding.

    `asyncio` is deliberately not forbidden. MatchmakingQueue guards its own
    pairing invariant with an `asyncio.Lock`, which is a concurrency primitive
    protecting domain state rather than a door to the outside world — the
    distinction this test cares about is I/O, not coroutines.
    """
    forbidden = {"websockets", "aiosqlite", "json", "bcrypt", "sqlite3", "socket"}
    violations = []

    for path in sorted((SERVER_ROOT / "domain").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.split(".")[0] in forbidden:
                    violations.append(f"{path.name}:{node.lineno} imports {name}")

    assert not violations, "domain must not depend on I/O:\n" + "\n".join(violations)


@pytest.mark.parametrize("layer", LAYERS)
def test_each_layer_package_documents_what_it_owns(layer):
    """CLAUDE.md requires every module to state its layer and its boundaries."""
    docstring = ast.get_docstring(
        ast.parse((SERVER_ROOT / layer / "__init__.py").read_text(encoding="utf-8"))
    )
    assert docstring, f"server/{layer}/__init__.py needs a module docstring"
    assert "Owns:" in docstring, f"server/{layer} must document what it owns"
    assert "Must not own:" in docstring, f"server/{layer} must document its boundary"
