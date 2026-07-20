"""Unit tests for the EventBus — the Subject side of the Observer pattern.

The bus is dispatched from inside the simulation tick, so the properties pinned
here are the ones that keep a misbehaving subscriber from reaching back into the
game: failures stay contained, and the subscriber list survives being mutated
while it is being iterated.
"""

import unittest

from shared.events import (
    Event,
    EventBus,
    GameEndedEvent,
    Observer,
    PieceCapturedEvent,
    PieceMovedEvent,
)
from shared.model.position import Position


def _moved(at_ms: int = 0) -> PieceMovedEvent:
    return PieceMovedEvent(
        at_ms=at_ms, color="w", piece_type="R",
        frm=Position(0, 0), to=Position(0, 1), was_capture=False,
    )


def _captured(at_ms: int = 0) -> PieceCapturedEvent:
    return PieceCapturedEvent(
        at_ms=at_ms, color="b", piece_type="P", pos=Position(0, 1),
        captor_color="w", captor_piece_type="R",
    )


class _Recorder(Observer):
    def __init__(self) -> None:
        self.seen: list = []

    def on_event(self, event: Event) -> None:
        self.seen.append(event)


class _Exploder(Observer):
    def on_event(self, event: Event) -> None:
        raise RuntimeError("subscriber blew up")


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.recorder = _Recorder()

    def test_an_unfiltered_subscriber_receives_every_event(self):
        self.bus.subscribe(self.recorder)
        self.bus.publish(_moved())
        self.bus.publish(_captured())
        self.assertEqual(len(self.recorder.seen), 2)

    def test_a_filtered_subscriber_receives_only_its_types(self):
        self.bus.subscribe(self.recorder, PieceCapturedEvent)
        self.bus.publish(_moved())
        self.bus.publish(_captured())
        self.assertEqual([type(e) for e in self.recorder.seen], [PieceCapturedEvent])

    def test_a_subscriber_may_name_several_types(self):
        self.bus.subscribe(self.recorder, PieceCapturedEvent, PieceMovedEvent)
        self.bus.publish(_moved())
        self.bus.publish(_captured())
        self.bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))
        self.assertEqual(len(self.recorder.seen), 2)

    def test_subscribing_to_the_base_type_receives_everything(self):
        """Matching is by isinstance, so Event is the catch-all."""
        self.bus.subscribe(self.recorder, Event)
        self.bus.publish(_moved())
        self.assertEqual(len(self.recorder.seen), 1)

    def test_publishing_with_no_subscribers_is_a_no_op(self):
        self.bus.publish(_moved())

    def test_unsubscribe_stops_delivery(self):
        self.bus.subscribe(self.recorder)
        self.bus.unsubscribe(self.recorder)
        self.bus.publish(_moved())
        self.assertEqual(self.recorder.seen, [])

    def test_unsubscribing_an_unknown_observer_is_ignored(self):
        self.bus.unsubscribe(self.recorder)

    def test_subscribers_are_notified_in_subscription_order(self):
        order = []
        first, second = _Recorder(), _Recorder()
        first.on_event = lambda e: order.append("first")
        second.on_event = lambda e: order.append("second")
        self.bus.subscribe(first)
        self.bus.subscribe(second)
        self.bus.publish(_moved())
        self.assertEqual(order, ["first", "second"])


class TestABrokenSubscriberIsContained(unittest.TestCase):
    """publish() runs mid-tick, so a raising observer must not abort the caller."""

    def setUp(self):
        self.bus = EventBus()

    def test_a_raising_subscriber_does_not_propagate(self):
        self.bus.subscribe(_Exploder())
        with self.assertLogs("shared.events", level="ERROR"):
            self.bus.publish(_moved())

    def test_a_raising_subscriber_does_not_stop_the_others(self):
        recorder = _Recorder()
        self.bus.subscribe(_Exploder())
        self.bus.subscribe(recorder)
        with self.assertLogs("shared.events", level="ERROR"):
            self.bus.publish(_moved())
        self.assertEqual(len(recorder.seen), 1)


class TestMutationDuringDispatch(unittest.TestCase):
    """Handlers legitimately subscribe, unsubscribe, and re-publish mid-dispatch."""

    def setUp(self):
        self.bus = EventBus()

    def test_a_handler_may_publish_a_derived_event(self):
        """What MaterialScoreTracker does: capture in, score out, same bus."""
        recorder = _Recorder()

        class Deriver(Observer):
            def on_event(inner_self, event: Event) -> None:
                if isinstance(event, PieceCapturedEvent):
                    self.bus.publish(GameEndedEvent(at_ms=1, reason="derived", winner="w"))

        self.bus.subscribe(Deriver(), PieceCapturedEvent)
        self.bus.subscribe(recorder)
        self.bus.publish(_captured())

        self.assertEqual(
            [type(e) for e in recorder.seen], [GameEndedEvent, PieceCapturedEvent]
        )

    def test_a_handler_may_unsubscribe_itself_mid_dispatch(self):
        recorder = _Recorder()

        class SelfRemoving(Observer):
            def on_event(inner_self, event: Event) -> None:
                self.bus.unsubscribe(inner_self)

        self.bus.subscribe(SelfRemoving())
        self.bus.subscribe(recorder)
        self.bus.publish(_moved())
        self.assertEqual(len(recorder.seen), 1)

    def test_a_handler_may_subscribe_a_new_observer_mid_dispatch(self):
        """The newcomer joins for later events, not the one being dispatched."""
        late = _Recorder()

        class Adder(Observer):
            def on_event(inner_self, event: Event) -> None:
                self.bus.subscribe(late)

        self.bus.subscribe(Adder(), PieceMovedEvent)
        self.bus.publish(_moved())
        self.assertEqual(late.seen, [])
        self.bus.publish(_captured())
        self.assertEqual(len(late.seen), 1)


class TestEventsAreValueObjects(unittest.TestCase):
    def test_events_are_frozen(self):
        with self.assertRaises(Exception):
            _moved().at_ms = 5

    def test_events_compare_by_value(self):
        self.assertEqual(_moved(at_ms=7), _moved(at_ms=7))
        self.assertNotEqual(_moved(at_ms=7), _moved(at_ms=8))


if __name__ == "__main__":
    unittest.main()
