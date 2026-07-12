# Repository: https://github.com/le7-3609/kfchess

import sys
from pathlib import Path

# Allow running `python /path/to/main.py` from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kungfu_chess.bootstrap import bootstrap

if __name__ == "__main__":
    bootstrap()