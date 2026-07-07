import sys

def main():
    # Valid pieces and colors based on the test cases
    # wK, bK, wR, bR, bQ, wN, bP, etc.
    # Colors: 'w', 'b'. Pieces: 'K', 'Q', 'R', 'B', 'N', 'P'
    valid_colors = {'w', 'b'}
    valid_pieces = {'K', 'Q', 'R', 'B', 'N', 'P'}

    lines = [line.strip() for line in sys.stdin]
    
    board_lines = []
    in_board = False
    in_commands = False
    commands = []

    for line in lines:
        if not line:
            continue
        if line.startswith("Board:"):
            in_board = True
            in_commands = False
            continue
        elif line.startswith("Commands:"):
            in_board = False
            in_commands = True
            continue
        
        if in_board:
            board_lines.append(line)
        elif in_commands:
            commands.append(line)

    if not board_lines:
        return

    # Parse and validate the board
    parsed_board = []
    expected_width = None

    for row_idx, line in enumerate(board_lines):
        tokens = line.split()
        if not tokens:
            continue
        
        # Validate row width consistency
        if expected_width is None:
            expected_width = len(tokens)
        elif len(tokens) != expected_width:
            print("ERROR ROW_WIDTH_MISMATCH")
            return

        # Validate each token
        for token in tokens:
            if token == '.':
                continue
            
            # Check length and valid piece/color format (e.g., "wK")
            if len(token) != 2 or token[0] not in valid_colors or token[1] not in valid_pieces:
                print("ERROR UNKNOWN_TOKEN")
                return

        parsed_board.append(tokens)

    # Process commands
    for cmd in commands:
        if cmd == "print board":
            for row in parsed_board:
                print(" ".join(row))

if __name__ == "__main__":
    main()