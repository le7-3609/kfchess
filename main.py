import sys

from kfchess.repository.in_memory import InMemoryBoardRepository, InMemoryGameStateRepository
from kfchess.services.parser import SimpleBoardParser
from kfchess.services.validator import BoardValidator
from kfchess.services.printer import ConsoleBoardPrinter
from kfchess.services.command_executor import CommandExecutor
from kfchess.services.game_service import GameService


def main() -> None:
    # Composition Root: wire all concrete classes via Dependency Injection.
    board_repo = InMemoryBoardRepository()
    state_repo = InMemoryGameStateRepository()
    parser = SimpleBoardParser()
    validator = BoardValidator()
    printer = ConsoleBoardPrinter()
    command_executor = CommandExecutor(
        board_repo=board_repo,
        state_repo=state_repo,
        printer=printer,
    )
    service = GameService(
        board_repo=board_repo,
        state_repo=state_repo,
        parser=parser,
        validator=validator,
        command_executor=command_executor,
    )

    input_lines = sys.stdin.readlines()
    result = service.execute(input_lines)

    if not result.is_ok:
        print(f"ERROR {result.error}")


if __name__ == "__main__":
    main()