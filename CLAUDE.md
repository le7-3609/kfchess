# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt      # Pillow, pytest, pre-commit

python main_gui.py                   # Tkinter GUI (prompts for player names, then plays)
python main.py                       # headless: reads a command script from stdin

pytest                               # full suite (~200 tests, a few seconds)
pytest tests/unit/test_board.py                      # one file
pytest tests/unit/test_board.py::TestBoard::test_x   # one test
pytest -k collision                                  # by name

pre-commit run --all-files           # the only hook is pytest; CI (.github/workflows/tests.yml) runs pytest on Python 3.10
```

There is no linter or formatter configured, and no pytest config file — discovery is pytest's default over `tests/`.

## What the game is

Kung Fu Chess is real-time chess with **no turns**: both colors move concurrently, pieces travel square-by-square over time rather than teleporting, and after arriving a piece sits in cooldown before it can move again. The rules that most often get broken by a careless change (documented in full in [README.md](README.md) and [.agents/AGENTS.md](.agents/AGENTS.md)):

- **Collisions**: two pieces on the same square, or crossing paths (swapping adjacent cells), must resolve. Priority is: an airborne (jumping) piece beats a non-jumping enemy; otherwise the piece that started moving *earlier* wins; ties break by list order/index. Enemy collisions capture; friendly collisions abort the loser into cooldown.
- **Airborne pieces** ignore path blocking and normal path collisions, and capture any enemy that tries to land on their square mid-jump.
- **Clock never flows backward**; guard division-by-zero in duration/interpolation math; don't let a moving piece's vacated origin square cause phantom deletions.

## Architecture

Clean Architecture with a strict numbered layering. **Every module's docstring names its layer and states what it must *not* own** — read it before editing; those constraints are the design, not decoration. Dependencies point inward only, and interfaces are declared in the inner layer that needs them (e.g. `PixelMapperInterface` lives in `engine/engine_interfaces.py`, not in `input/`, so engine never imports input).

| Layer | Package | Role |
|---|---|---|
| 1–2 | [model/](kungfu_chess/model/), [config/](kungfu_chess/config/) | `Position`, `ArrayBoard`, `TextPiece`, `GameState`, `Movement`, `Cooldown`, `Result`; `GameConfig` timing constants |
| 2–3 | [rules/](kungfu_chess/rules/) | pure legality/math: per-piece validators, `PathChecker`, `ThreatValidator`, `EndgameValidator`, `CastlingValidator` |
| 4 | [realtime/](kungfu_chess/realtime/) | `RealTimeArbiter` tick loop + `CollisionResolver`, `ArrivalResolver`, `ProxyBoard`, duration strategies |
| 5 | [engine/](kungfu_chess/engine/) | `GameEngine` command dispatch, click/jump/castling processors, game-over detection |
| 6 | [input/](kungfu_chess/input/), [view/](kungfu_chess/view/), [ui/](kungfu_chess/ui/) | pixel↔cell mapping, `GameSnapshot` DTO, Pillow renderer, Tk window |
| 7 | [io/](kungfu_chess/io/) | board parse/print, moves log, JSON history store, replay decorator |
| 8–9 | [texttests/](kungfu_chess/texttests/), [runtime/](kungfu_chess/runtime/) | `.kfc` script runner; asyncio tick loop |

The core is a pure simulation engine with no UI or network dependency. Keep it that way — a networked server should be addable without touching `rules/`, `realtime/`, or `engine/`.

### The two boundaries that matter

**[service.py](kungfu_chess/service.py) — `GameService` is the only public entry point.** The Tk window, script runner, and bots all talk exclusively to it: commands (`init_game`, `execute_command`, `click`, `right_click`, `advance_clock`, history save/load) and queries (`get_snapshot`, `get_moves`, `list_saves`). Nothing outside reaches through to `GameEngine`, the repositories, or the arbiter. Optional collaborators (`arbiter`, `moves_log`, `history_store`) gate only the query/history methods — the pure `execute()` path used by text tests works without them.

**[bootstrap.py](kungfu_chess/bootstrap.py) — the composition root.** All wiring happens here; nothing else constructs the object graph.
- `build_core(...)` returns `CoreComponents`, the shared stack.
- `build_service()` — `InstantMovementDuration`, for tests and scripted runs.
- `build_realtime_service()` — `ChebyshevDistanceDuration`, pieces travel over time; adds moves log + history store.
- Bots need the *same* repo/arbiter instances, so they can't be injected after the fact — [bot_factory.py](kungfu_chess/bot_factory.py) composes `build_core()` with bot construction instead.

### Non-obvious invariants

- **Active motions live in `RealTimeArbiter`, not `GameState`.** Go through `register_motion` / `movements` / `remove_motion`; never mutate a shared list.
- **Selection state (`GameState.selected_pos`) is owned by `GameEngine`.** The `Controller` is a stateless click→command translator, and the Tk window keeps no selection — it arrives back via the snapshot.
- **`ProxyBoard` overlays in-flight motions on demand** (O(active_motions) per lookup) instead of copying the board each simulation step. Use `arbiter.get_effective_board(...)` for "where is everything right now".
- **`GameEngine._resolve_pending` deliberately skips the game-end scan** unless piece positions or cooldown membership changed — the checkmate/stalemate scan is expensive enough to blow the 16ms render budget if run on every idle tick. Don't make it unconditional.
- **The rendering path is one-way**: `advance_clock` → `SnapshotBuilder` builds an immutable `GameSnapshot` → `PillowRenderer` composes an `Img` → tkinter only displays the finished frame. Legal-move highlighting reuses `GameEngine.legal_moves_from` rather than reimplementing rules in the view.
- **`GameService._adjust_pawn_rules_for_board_height`** rewrites pawn start rows/promotion ranks per installed board, since text-test boards are often smaller than 8×8.

### Text scripts (`.kfc`)

Integration tests in [tests/integration/scripts/](tests/integration/scripts/) are the executable spec for the rules. Format: a `Board:` block of tokens (`wK`, `bR`, `.`), a `Commands:` block (`click X Y`, `right_click X Y`, `wait MS`, `print board` — pixel coordinates, unrecognized lines are ignored by design), and an `Expected:` board block. They run through `build_service()` (instant movement) with `require_kings=False`. Adding a rule case here is usually better than a unit test

## Conventions & Clean Code

- **Clean Code & Single Responsibility:** Keep functions short and highly focused. If a function has more than one responsibility, or contains nested complex logic, **you must split it** and extract the logic into private helper functions with clear names.
- **Self-Documenting Code:** Good code is like a book that tells its own story. Prioritize readable code with descriptive variable and function names over comments.
- **Comment Rules (Strict):**
  - **DELETE "What" comments:** Remove inline comments that simply explain *what* the code is doing. Refactor the code to be expressive enough that it doesn't need them.
  - **KEEP "Why" comments:** Preserve and write comments that explain *why* a decision was made (e.g., business logic, non-obvious invariants, or performance hacks).
- **Docstrings:** Always use standard docstrings for modules, classes, and functions to explain their purpose, expected inputs, and outputs. Keep these; they are the correct way to document APIs.
- **Error Handling:** Errors flow back as `Result.ok/fail` (see `model/game_state.py`), not exceptions, on the command path.
- **Testing:** Every feature, fix, or refactor needs test coverage, and the suite must stay green. It exists to pin collision priorities and rule edge cases across refactors.

## Strict Design Principles & Boundaries

- **High-Scale & Loose Coupling:** Architect for scale. Maintain extremely loose coupling between components. Minimize direct dependencies, avoid mixing domains, and prefer composition over inheritance.
- **Strict Boundaries (Horizontal & Vertical):**
  - **Layer Boundaries:** Layers are strictly isolated. Data crossing layers must be properly mapped or validated.
  - **Domain Model Boundaries:** Boundaries are not just between layers, but between classes within the same layer. Inside the Domain Model, every class must have a highly specific responsibility. Do not bleed logic between domain entities.
- **Gatekeeping & Validation (Fail Fast):** 
  - Every class acts as a "gatekeeper" for its own data and invariants. Clearly define which component is responsible for which validation.
  - **Fail Fast:** Validate inputs and state immediately. If something is wrong, fail at the exact point of origin (where the problem was found). Never drag invalid state or errors across layers.
- **Traceability:** The execution flow must be easy to trace and reason about. Avoid hidden side-effects, implicit state mutations, or overly "clever" dynamic metaprogramming that obscures *where* and *why* things happen.

## Conventions

- PEP-8, type annotations, and the existing docstring style: a module docstring stating layer + owns/must-not-own, and comments that explain *why* for real-time simulation subtleties.
- Errors flow back as `Result.ok/fail` (see [model/game_state.py](kungfu_chess/model/game_state.py)), not exceptions, on the command path.
- Every feature, fix, or refactor needs test coverage, and the suite must stay green — it exists to pin collision priorities and rule edge cases across refactors.
