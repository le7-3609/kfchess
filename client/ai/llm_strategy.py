"""LLM-backed move selection (client layer), provider-agnostic.

Owns: turning a board + legal-move list into a prompt, and a model reply back
into one of those moves, without ever blocking the Tk loop — the HTTP call runs
on a background worker while choose_move returns None ("no decision yet").
Must not own: legality (the rules layer produced the list it picks from, and
StrategyBotInputSource discards anything outside it), pacing, provider choice
(providers.py), or transport details (chat_client.py). The strategy only sees
ChatClientInterface, so swapping Groq for OpenAI or any other provider never
touches this module.

Implements shared's BotStrategyInterface, keeping the dependency arrow
client -> shared.

Failure policy: the bot must never freeze. A garbage or failed reply falls back
to the random strategy for that turn, and after enough consecutive transport
failures the client is dropped entirely and the fallback plays on.
"""

import logging
import re
import threading
from pathlib import Path
from typing import Callable, List, Optional

from shared.config import consts
from shared.input.bot_strategy import BotStrategyInterface, Move, RandomMoveStrategy
from shared.model.board import BoardInterface
from shared.model.game_state import GameState
from shared.model.position import Position
from client.ai.chat_client import ChatClientInterface
from client.ai.env_loader import DEFAULT_ENV_FILE
from client.ai.providers import build_chat_client

_LOGGER = logging.getLogger(__name__)

MAX_CONSECUTIVE_TRANSPORT_FAILURES = 3
_EMPTY_SQUARE_TOKEN = ".."

_SYSTEM_PROMPT = (
    "You are playing Kung Fu Chess, a real-time chess variant with no turns. "
    "You will be given the current board and a numbered list of your legal moves. "
    "Reply with ONLY the number of the move you choose."
)


def _spawn_daemon_thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, daemon=True).start()


class _PendingReply:
    """One in-flight completion: the request's move list plus its eventual outcome.

    The worker thread writes reply/error and flips ``done`` last; the Tk thread
    only reads the fields after seeing ``done``, so this single-writer handoff
    needs no lock.
    """

    def __init__(self, moves: List[Move]) -> None:
        self.moves = moves
        self.reply: Optional[str] = None
        self.error: Optional[Exception] = None
        self.done = False


class LlmMoveStrategy:
    """Asks an LLM to pick one of the bot's legal moves.

    choose_move is non-blocking by contract: with no reply ready it starts (or
    keeps waiting on) a background request and returns None, which the input
    source treats as "no decision yet"; the paced wrapper retries shortly after.
    A reply computed for a position that has since changed is discarded and a
    fresh request is made about the board as it stands now.
    """

    def __init__(
        self,
        color: str,
        client: ChatClientInterface,
        fallback: Optional[BotStrategyInterface] = None,
        start_worker: Callable[[Callable[[], None]], None] = _spawn_daemon_thread,
    ) -> None:
        self._color = color
        self._client: Optional[ChatClientInterface] = client
        self._fallback = fallback or RandomMoveStrategy()
        self._start_worker = start_worker
        self._pending: Optional[_PendingReply] = None
        self._consecutive_failures = 0

    def choose_move(
        self, legal_moves: List[Move], board: BoardInterface, state: GameState
    ) -> Optional[Move]:
        if not legal_moves:
            return None
        if self._client is None:
            return self._fallback.choose_move(legal_moves, board, state)
        if self._pending is not None:
            if not self._pending.done:
                return None
            return self._settle_reply(legal_moves, board, state)
        self._request_decision(legal_moves, board)
        return None

    def _settle_reply(
        self, legal_moves: List[Move], board: BoardInterface, state: GameState
    ) -> Optional[Move]:
        """Turn a finished request into a move, a fallback pick, or a retry."""
        pending, self._pending = self._pending, None
        if pending.error is not None:
            self._register_failure(pending.error)
            return self._fallback.choose_move(legal_moves, board, state)
        self._consecutive_failures = 0

        move = self._parse_reply(pending.reply, pending.moves)
        if move is None:
            _LOGGER.warning("LLM reply %r named no legal move; falling back.", pending.reply)
            return self._fallback.choose_move(legal_moves, board, state)
        if move not in legal_moves:
            # The position changed while the model was thinking; ask again
            # about the board as it stands now rather than play a stale move.
            self._request_decision(legal_moves, board)
            return None
        return move

    def _register_failure(self, error: Exception) -> None:
        self._consecutive_failures += 1
        _LOGGER.warning(
            "LLM request failed (%d/%d): %s",
            self._consecutive_failures,
            MAX_CONSECUTIVE_TRANSPORT_FAILURES,
            error,
        )
        if self._consecutive_failures >= MAX_CONSECUTIVE_TRANSPORT_FAILURES:
            _LOGGER.warning("Dropping the LLM client; the fallback strategy plays on.")
            self._client = None

    def _request_decision(self, legal_moves: List[Move], board: BoardInterface) -> None:
        """Fire one background completion for the current position."""
        pending = _PendingReply(list(legal_moves))
        prompt = self._build_prompt(pending.moves, board)
        client = self._client

        def job() -> None:
            # The worker must never leak an exception into a dying thread —
            # any failure becomes a recorded error the Tk thread settles later.
            try:
                pending.reply = client.complete(_SYSTEM_PROMPT, prompt)
            except Exception as exc:  # noqa: BLE001
                pending.error = exc
            finally:
                pending.done = True

        self._pending = pending
        self._start_worker(job)

    def _build_prompt(self, moves: List[Move], board: BoardInterface) -> str:
        color_name = "white" if self._color == consts.COLOR_WHITE else "black"
        lines = [
            f"You play {color_name}. Board is {board.rows}x{board.cols}, row 0 at the top; "
            f"tokens are color+piece (e.g. wK = white king), {_EMPTY_SQUARE_TOKEN} is empty:",
            self._board_text(board),
            "Your legal moves, as (row,col) -> (row,col):",
        ]
        for index, (source, target) in enumerate(moves, start=1):
            piece = board.get_piece(source)
            piece_token = f"{piece.color}{piece.piece_type}" if piece else "?"
            lines.append(
                f"{index}. {piece_token} ({source.row},{source.col}) -> ({target.row},{target.col})"
            )
        lines.append("Reply with only the number of your chosen move.")
        return "\n".join(lines)

    def _board_text(self, board: BoardInterface) -> str:
        rows = []
        for row in range(board.rows):
            tokens = []
            for col in range(board.cols):
                piece = board.get_piece(Position(row, col))
                tokens.append(
                    f"{piece.color}{piece.piece_type}" if piece else _EMPTY_SQUARE_TOKEN
                )
            rows.append(" ".join(tokens))
        return "\n".join(rows)

    def _parse_reply(self, reply: Optional[str], moves: List[Move]) -> Optional[Move]:
        """The 1-based index the model named, resolved against the request's move list."""
        if not reply:
            return None
        match = re.search(r"\d+", reply)
        if match is None:
            return None
        index = int(match.group()) - 1
        if not 0 <= index < len(moves):
            return None
        return moves[index]


def build_llm_strategy(
    bot_color: str, env_file: Path = DEFAULT_ENV_FILE
) -> Optional[LlmMoveStrategy]:
    """The LLM-difficulty strategy for *bot_color*, or None with no API key configured.

    Composed here rather than in shared/bot_factory because the transport may
    not cross into shared/; the lobby passes the result through
    build_bot_controller(..., bot_strategy=...). Which provider answers is the
    registry's decision (providers.py), not this module's.
    """
    client = build_chat_client(env_file)
    if client is None:
        return None
    return LlmMoveStrategy(color=bot_color, client=client)
