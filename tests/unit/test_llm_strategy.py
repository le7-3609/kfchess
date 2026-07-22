"""Unit tests for LlmMoveStrategy — no thread and no network anywhere.

The worker is a manual queue the test pumps by hand, so pending/done timing is
fully deterministic, and the client is a scripted fake, so prompts and replies
are inspected without any HTTP.
"""

from client.ai.chat_client import ChatClientError
from client.ai.llm_strategy import (
    LlmMoveStrategy,
    MAX_CONSECUTIVE_TRANSPORT_FAILURES,
)
from shared.config import consts
from shared.model.game_state import GameState
from shared.model.position import Position


class _Piece:
    def __init__(self, color: str, piece_type: str) -> None:
        self.color = color
        self.piece_type = piece_type


class _Board:
    def __init__(self, rows: int = 3, cols: int = 3, pieces: dict = None) -> None:
        self.rows = rows
        self.cols = cols
        self._pieces = pieces or {}

    def get_piece(self, pos: Position):
        return self._pieces.get((pos.row, pos.col))


class _ScriptedClient:
    """Returns (or raises) queued outcomes; records every prompt it was sent."""

    def __init__(self) -> None:
        self.outcomes = []
        self.prompts = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append(user_prompt)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _ManualWorker:
    """Captures jobs instead of threading them; the test runs them when it chooses."""

    def __init__(self) -> None:
        self.jobs = []

    def __call__(self, job) -> None:
        self.jobs.append(job)

    def run_next(self) -> None:
        self.jobs.pop(0)()


class _FirstMoveFallback:
    """A deterministic fallback so tests can tell a fallback pick from a model pick."""

    def choose_move(self, legal_moves, board, state):
        return legal_moves[0]


def _make_strategy(client=None, worker=None):
    client = client if client is not None else _ScriptedClient()
    worker = worker if worker is not None else _ManualWorker()
    strategy = LlmMoveStrategy(
        color=consts.COLOR_BLACK,
        client=client,
        fallback=_FirstMoveFallback(),
        start_worker=worker,
    )
    return strategy, client, worker


def _moves():
    return [
        (Position(1, 0), Position(2, 0)),
        (Position(1, 1), Position(2, 1)),
        (Position(1, 2), Position(2, 2)),
    ]


def _board():
    return _Board(pieces={(1, 0): _Piece("b", "P"), (0, 0): _Piece("b", "K")})


class TestRequestLifecycle:
    def test_first_call_starts_a_request_and_returns_none(self):
        strategy, client, worker = _make_strategy()
        client.outcomes.append("1")

        assert strategy.choose_move(_moves(), _board(), GameState()) is None
        assert len(worker.jobs) == 1

    def test_prompt_carries_the_board_and_the_numbered_legal_moves(self):
        strategy, client, worker = _make_strategy()
        client.outcomes.append("1")

        strategy.choose_move(_moves(), _board(), GameState())
        worker.run_next()

        prompt = client.prompts[0]
        assert "bK .. .." in prompt  # board row 0
        assert "1. bP (1,0) -> (2,0)" in prompt
        assert "3." in prompt
        assert "black" in prompt

    def test_while_pending_no_second_request_is_started(self):
        strategy, client, worker = _make_strategy()
        client.outcomes.append("1")

        strategy.choose_move(_moves(), _board(), GameState())
        assert strategy.choose_move(_moves(), _board(), GameState()) is None
        assert len(worker.jobs) == 1

    def test_a_valid_index_reply_becomes_that_move(self):
        strategy, client, worker = _make_strategy()
        client.outcomes.append("2")

        strategy.choose_move(_moves(), _board(), GameState())
        worker.run_next()

        assert strategy.choose_move(_moves(), _board(), GameState()) == _moves()[1]

    def test_no_legal_moves_returns_none_without_a_request(self):
        strategy, _client, worker = _make_strategy()

        assert strategy.choose_move([], _board(), GameState()) is None
        assert worker.jobs == []


class TestBadReplies:
    def test_a_garbage_reply_falls_back(self):
        strategy, client, worker = _make_strategy()
        client.outcomes.append("I resign, good sir")

        strategy.choose_move(_moves(), _board(), GameState())
        worker.run_next()

        assert strategy.choose_move(_moves(), _board(), GameState()) == _moves()[0]

    def test_an_out_of_range_index_falls_back(self):
        strategy, client, worker = _make_strategy()
        client.outcomes.append("99")

        strategy.choose_move(_moves(), _board(), GameState())
        worker.run_next()

        assert strategy.choose_move(_moves(), _board(), GameState()) == _moves()[0]

    def test_a_stale_reply_is_discarded_and_a_fresh_request_made(self):
        strategy, client, worker = _make_strategy()
        client.outcomes.extend(["1", "2"])

        strategy.choose_move(_moves(), _board(), GameState())
        worker.run_next()

        # The position changed: the move the model picked no longer exists.
        changed = _moves()[1:]
        assert strategy.choose_move(changed, _board(), GameState()) is None
        assert len(worker.jobs) == 1  # a fresh request was queued (the first was consumed)


class TestTransportFailures:
    def test_a_failed_request_falls_back_for_that_turn(self):
        strategy, client, worker = _make_strategy()
        client.outcomes.append(ChatClientError("boom"))

        strategy.choose_move(_moves(), _board(), GameState())
        worker.run_next()

        assert strategy.choose_move(_moves(), _board(), GameState()) == _moves()[0]

    def test_repeated_failures_drop_the_client_for_good(self):
        strategy, client, worker = _make_strategy()
        client.outcomes.extend(
            [ChatClientError("boom")] * MAX_CONSECUTIVE_TRANSPORT_FAILURES
        )

        for _ in range(MAX_CONSECUTIVE_TRANSPORT_FAILURES):
            strategy.choose_move(_moves(), _board(), GameState())  # start request
            worker.run_next()
            strategy.choose_move(_moves(), _board(), GameState())  # settle -> fallback

        jobs_before = len(worker.jobs)
        # The client is gone: the fallback answers immediately, no new request.
        assert strategy.choose_move(_moves(), _board(), GameState()) == _moves()[0]
        assert len(worker.jobs) == jobs_before
