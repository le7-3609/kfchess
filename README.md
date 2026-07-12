# Kung Fu Chess

Kung Fu Chess is a real-time, no-turns variation of traditional chess. Unlike classical chess, any piece can be moved at any time, adding a fast-paced action element to the profound strategy of the original game.

---
## Game Rules (What makes a legal Kung Fu Chess game?)

The core gameplay centers on continuous action where players do not wait for turns. The rules of this simulation are defined as follows:

* **Real-Time (No Turns)**: There are no player turns. Both players ("w" and "b") can move pieces concurrently at any clock time.
* **Travel Duration**: Pieces do not teleport. They travel square-by-square over time. The travel duration is defined by their speed configurations (e.g., using Chebyshev distance).
* **Collisions**:
  * **Same-Square Collision**: Two pieces arriving or occupying the same square at the same time must resolve collisions (winner lands/captures, loser is captured or aborted/returned).
  * **Crossing Collision**: If two pieces cross paths (swap adjacent cells), they must collide and resolve.
  * **Resolution Priority**: A jumping (airborne) piece wins against a non-jumping piece of the opponent. Otherwise, the piece that started moving earlier wins. If they started at the same time, break ties using their list order or index.
* **Jumping (Airborne state)**:
  * A jumping piece (e.g., Knight, or piece jumping in-place) is "airborne."
  * It is immune to intermediate path blocking and regular path collisions while in the air.
  * If an enemy piece attempts to land on the jumping piece's square while it is airborne, the jumping piece captures the landing piece.
* **Cooldown**: After a piece arrives at its destination or completes a jump, it enters a cooldown period. During this time, the piece cannot be selected or moved. The cooldown duration is configurable (e.g., via `cooldown_duration_ms`).

---
## Architecture and Design Patterns

The codebase is built on **Clean Architecture** and follows **SOLID** design principles. The backend engine is completely decoupled from any UI or network logic, making it a pure, math-driven simulation engine.

The system is separated into distinct layers:
* **Models**: Entities holding data and basic states (`TextPiece`, `ArrayBoard`, `Movement`, `GameState`, `Result`).
* **Repositories**: Decouple state storage (`InMemoryBoardRepository`, `InMemoryGameStateRepository`) from the game logic.
* **Rules (Domain)**: Pure validation logic and math calculations (`MoveValidators`, `PathChecker`, `PromotionRules`).
* **Services (Application)**: Coordination, parsing, command execution, and movement management (`GameService`, `CommandExecutor`, `MovementManager`).

This strict separation guarantees that the core game logic is untouched by outer layer details like input parsing or rendering, meaning that replacing the storage, changing the interface, or adding a network layer can be done without modifying the rules or core logic.

---
## Technological Stack & Current Status

* **Language**: Python 3.
* **Current Status**: At the moment, the project consists **only of the backend simulation engine and a local graphical/textual interface**. It currently **does not have the social capabilities of a networked server** (such as matchmakers, user accounts, or multiplayer over the internet). The UI and network layers can be introduced independently later.

---
## Tests

The project puts a heavy emphasis on reliability and mathematical accuracy, given the edge cases of real-time movement (e.g., division by zero guards, precise collision resolutions).
* **Framework**: `pytest`
* **Coverage**: Extensive unit and integration tests are present. The test suite is designed to ensure the existing rule set, validation logic, and collision priorities remain strictly enforced during refactors. Always run `pytest` to verify the simulation's correctness after making changes.

---

## License

This project is licensed under the [MIT License](LICENSE).
