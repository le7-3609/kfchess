import sys
from kfchess.repository import InMemoryBoardRepository
from kfchess.services import (
    SimpleBoardParser,
    BoardValidator,
    ConsoleBoardPrinter,
    GameService
)

def main():
    # Composition Root: Wires up all components using Dependency Injection (DI)
    repository = InMemoryBoardRepository()
    parser = SimpleBoardParser()
    validator = BoardValidator()
    printer = ConsoleBoardPrinter()

    service = GameService(
        repository=repository,
        parser=parser,
        validator=validator,
        printer=printer
    )

    # Read from standard input
    input_lines = sys.stdin.readlines()

    # Execute service logic
    result = service.execute(input_lines)

    # Hand off error output handling
    if not result.is_ok:
        print(f"ERROR {result.error}")

if __name__ == "__main__":
    main()