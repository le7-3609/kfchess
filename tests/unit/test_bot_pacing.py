"""Unit tests for PacedBotInputSource and StrategyBotInputSource gatekeeping.

Pacing is asserted against the *game* clock (a controllable fake state), never
call count, and gatekeeping proves an out-of-set strategy pick never becomes a
command.
"""

import pytest

from shared.input.bot import PacedBotInputSource, StrategyBotInputSource
from shared.engine.input_commands import RequestMoveCommand
from shared.model.game_state import GameState
from shared.model.position import Position


class _FakeStateRepo:
    """A state repo whose clock the test drives directly."""

    def __init__(self):
        self.state = GameState()

    def get_state(self) -> GameState:
        return self.state

    def save_state(self, state: GameState) -> None:
        self.state = state

    def set_clock(self, ms: int) -> None:
        self.state.clock_ms = ms


class _FakeInner:
    def __init__(self, commands):
        self._commands = commands
        self.poll_count = 0

    def get_next_commands(self):
        self.poll_count += 1
        return list(self._commands)


class TestPacing:
    def _paced(self, repo, inner, interval=1000):
        return PacedBotInputSource(inner, repo, move_interval_ms=interval)

    def test_first_poll_establishes_a_baseline_and_emits_nothing(self):
        repo = _FakeStateRepo()
        inner = _FakeInner(["move"])
        paced = self._paced(repo, inner)

        assert paced.get_next_commands() == []
        assert inner.poll_count == 0  # the inner source is not even consulted yet

    def test_no_command_before_the_interval_elapses(self):
        repo = _FakeStateRepo()
        inner = _FakeInner(["move"])
        paced = self._paced(repo, inner, interval=1000)

        paced.get_next_commands()  # baseline at t=0
        repo.set_clock(999)

        assert paced.get_next_commands() == []
        assert inner.poll_count == 0

    def test_exactly_one_batch_once_the_interval_elapses(self):
        repo = _FakeStateRepo()
        inner = _FakeInner(["move"])
        paced = self._paced(repo, inner, interval=1000)

        paced.get_next_commands()  # baseline at t=0
        repo.set_clock(1000)
        first = paced.get_next_commands()

        repo.set_clock(1500)
        second = paced.get_next_commands()  # only 500ms later -> too soon

        assert first == ["move"]
        assert second == []
        assert inner.poll_count == 1

    def test_interval_is_measured_from_the_last_emission(self):
        repo = _FakeStateRepo()
        inner = _FakeInner(["move"])
        paced = self._paced(repo, inner, interval=1000)

        paced.get_next_commands()  # baseline at t=0
        repo.set_clock(1000)
        paced.get_next_commands()  # emits, resets baseline to 1000
        repo.set_clock(2000)

        assert paced.get_next_commands() == ["move"]
        assert inner.poll_count == 2

    def test_rejects_a_non_positive_interval(self):
        with pytest.raises(ValueError):
            PacedBotInputSource(_FakeInner([]), _FakeStateRepo(), move_interval_ms=0)

    def test_an_empty_answer_backs_off_instead_of_retrying_every_tick(self):
        """A waiting inner source (e.g. an LLM reply in flight) is polled at the
        backoff beat, not on every advance_clock tick."""
        repo = _FakeStateRepo()
        inner = _FakeInner([])
        paced = PacedBotInputSource(
            inner, repo, move_interval_ms=1000, empty_retry_ms=250
        )

        paced.get_next_commands()  # baseline at t=0
        repo.set_clock(1000)
        paced.get_next_commands()  # consulted -> empty, backoff begins

        repo.set_clock(1100)
        paced.get_next_commands()  # inside the backoff window
        assert inner.poll_count == 1

        repo.set_clock(1250)
        paced.get_next_commands()  # backoff elapsed -> consulted again
        assert inner.poll_count == 2

    def test_the_backoff_never_exceeds_the_move_interval(self):
        repo = _FakeStateRepo()
        inner = _FakeInner([])
        paced = PacedBotInputSource(
            inner, repo, move_interval_ms=100, empty_retry_ms=250
        )

        paced.get_next_commands()  # baseline at t=0
        repo.set_clock(100)
        paced.get_next_commands()  # consulted -> empty

        repo.set_clock(200)  # one full interval later
        paced.get_next_commands()
        assert inner.poll_count == 2


class _FakeBoardRepo:
    def __init__(self, board):
        self._board = board

    def get_board(self):
        return self._board

    def save_board(self, board):
        self._board = board


class _FakeEndgameValidator:
    def __init__(self, legal_moves):
        self._legal_moves = legal_moves

    def get_legal_moves(self, board, state, color):
        return list(self._legal_moves)


class _FixedStrategy:
    def __init__(self, move):
        self._move = move

    def choose_move(self, legal_moves, board, state):
        return self._move


class TestGatekeeping:
    def _source(self, legal_moves, strategy):
        repo_board = _FakeBoardRepo(board=object())
        repo_state = _FakeStateRepo()
        return StrategyBotInputSource(
            color="w",
            board_repo=repo_board,
            state_repo=repo_state,
            endgame_validator=_FakeEndgameValidator(legal_moves),
            strategy=strategy,
            arbiter=None,
        )

    def test_a_legal_pick_becomes_a_move_command(self):
        move = (Position(6, 4), Position(4, 4))
        source = self._source([move], _FixedStrategy(move))

        commands = source.get_next_commands()

        assert commands == [RequestMoveCommand(source=move[0], target=move[1])]

    def test_an_out_of_set_pick_is_discarded(self):
        legal = (Position(6, 4), Position(4, 4))
        illegal = (Position(0, 0), Position(7, 7))
        source = self._source([legal], _FixedStrategy(illegal))

        assert source.get_next_commands() == []

    def test_a_none_decision_yields_no_command(self):
        legal = (Position(6, 4), Position(4, 4))
        source = self._source([legal], _FixedStrategy(None))

        assert source.get_next_commands() == []
