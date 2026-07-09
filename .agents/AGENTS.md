# Antigravity Rules for Kung-Fu Chess (kfchess)

When working on this codebase, Antigravity must strictly adhere to the following rules, ensuring the integrity of the Kung-Fu Chess game rules, system architecture, and code quality.

---

## 1. Kung Fu Chess Game Rules

All modifications to movement, collision, or state resolution must respect the core real-time rules of Kung Fu Chess:
* **Real-Time (No Turns)**: There are no player turns. Both players ("w" and "b") can move pieces concurrently at any clock time.
* **Travel Duration**: Pieces do not teleport (unless configured with a 0-duration instant strategy). They travel square-by-square over time. The travel duration is defined by their speed configurations (e.g., Chebyshev distance duration).
* **Collisions**:
  * **Same-Square Collision**: Two pieces arriving or occupying the same square at the same time must resolve collisions (winner lands/captures, loser is captured or aborted/returned).
  * **Crossing Collision**: If two pieces cross paths (adjacent cell swapping), they must collide and resolve.
  * **Resolution Priority**: A jumping (airborne) piece wins against a non-jumping piece of the opponent. Otherwise, the piece that started moving earlier wins. If they started at the same time, break ties using their list order or index.
* **Jumping (Airborne state)**:
  * A jumping piece (e.g., Knight, or piece jumping in-place) is "airborne."
  * It is immune to intermediate path blocking and regular path collisions.
  * If an enemy piece attempts to land on the jumping piece's square while it is airborne, the jumping piece captures the landing piece.

---

## 2. Architecture & Design Principles

* **Clean Architecture & SOLID**: Preserve the separation of layers:
  * **Models**: Entities holding data and basic states (`TextPiece`, `ArrayBoard`, `Movement`, `GameState`, `Result`).
  * **Repositories**: Decouple state storage (`InMemoryBoardrepositories`, `InMemoryGameStaterepositories`) from game logic.
  * **Rules (Domain)**: Pure validation logic and math (`MoveValidators`, `PathChecker`, `PromotionRules`).
  * **Services (Application)**: Coordination, parsing, command execution, and movement management (`GameService`, `CommandExecutor`, `MovementManager`).
* **UI/Network Decoupling**: The backend must remain a pure simulation engine, independent of visual representations, networking sockets, or matchmakers.
* **Math & Edge Cases Safety**:
  * Guard against division-by-zero errors in movement calculations (e.g., when speeds or distances are small).
  * Ensure the clock never flows backward (validate wait command parameters).
  * Prevent "phantom deletions" (ensure moving pieces are handled correctly when their starting squares are occupied by other pieces).
* **Test Coverage**:
  * Every feature, bug fix, or refactor must be backed by appropriate unit or integration tests.
  * Keep the existing test suite passing (always run `pytest` to verify correctness).

---

## 3. Code Cleanliness & Conventions

* Use clean, readable, PEP-8 compliant Python code with clear type annotations.
* Maintain consistency in directory and file names. If standardizing class names (e.g., renaming `BoardrepositoriesInterface` to `BoardRepositoryInterface`), apply it consistently across all files and test suites.
* Preserve docstrings and comments explaining complex real-time simulation logic.

