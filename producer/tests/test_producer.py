"""
Unit tests for the trade-stream producer (producer/producer.py).

Run from the repo root with either:
    python -m pytest producer/tests/test_producer.py -v
    python -m unittest producer.tests.test_producer -v
(or simply: python -m unittest discover -s producer/tests -v)

The WebSocket is faked: a scripted FakeWebSocketApp is driven through the exact
on_open/on_message/on_error/on_close callback path that the real
websocket-client library invokes, so no network or Kafka broker is required.
"""

import json
import logging
import os
import sys
import unittest

# Make the producer package importable regardless of the current working dir.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import producer as producer_mod  # noqa: E402
from producer import (  # noqa: E402
    ExponentialBackoff,
    MSG_BAD_FRAME,
    MSG_NON_TRADE,
    MSG_TRADE,
    RoundStats,
    TradeStreamCollector,
    parse_trade_message,
    run_producer,
)


# --------------------------------------------------------------------------- #
# Helpers / fakes
# --------------------------------------------------------------------------- #
def make_trade_message(
    trade_id=12345,
    symbol="BTCUSDT",
    price="100.50",
    quantity="2.5",
    trade_time=1699999999999,
    is_buyer_maker=True,
):
    """A valid Binance-style trade frame."""
    return json.dumps(
        {
            "e": "trade",
            "E": 1699999999998,
            "s": symbol,
            "t": trade_id,
            "p": price,
            "q": quantity,
            "T": trade_time,
            "m": is_buyer_maker,
        }
    )


BAD_JSON_MESSAGE = "{this is not valid json"
NON_TRADE_MESSAGE = json.dumps({"e": "24hrTicker", "s": "BTCUSDT"})
SUBSCRIBE_ACK = json.dumps({"result": None, "id": 1})


class FakeKafkaProducer:
    """Records sends; can be configured to raise like an unavailable broker."""

    def __init__(self, fail_send=False):
        self.sent = []
        self.fail_send = fail_send

    def send(self, topic, value):
        if self.fail_send:
            raise RuntimeError("simulated Kafka outage")
        self.sent.append((topic, value))


class FakeWebSocketApp:
    """
    Minimal WebSocketApp stand-in that executes a scripted round through the
    real collector callbacks registered by TradeStreamCollector.run_forever.
    """

    def __init__(self, url, on_open, on_message, on_error, on_close, script):
        self.url = url
        self.on_open = on_open
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.script = script
        self.subscriptions = []

    def send(self, payload):
        self.subscriptions.append(payload)

    def run_forever(self):
        script = self.script
        if script.get("open", True):
            self.on_open(self)
        for message in script.get("messages", []):
            self.on_message(self, message)
        if "error" in script:
            self.on_error(self, script["error"])
        if script.get("close", False):
            self.on_close(self, 1006, "fake connection closed")
        if script.get("raise"):
            raise script["raise"]


class FakeWSFactory:
    """Hands out one FakeWebSocketApp per connection round, per its scripts."""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.apps = []

    def __call__(self, url, on_open, on_message, on_error, on_close):
        if not self.scripts:
            raise AssertionError("unexpected extra connection attempt")
        app = FakeWebSocketApp(
            url, on_open, on_message, on_error, on_close, self.scripts.pop(0)
        )
        self.apps.append(app)
        return app


class FakeConnectionError(Exception):
    pass


def build_collector(kafka_producer, scripts, initial_delay=5, max_delay=60):
    sleeps = []
    factory = FakeWSFactory(scripts)
    collector = TradeStreamCollector(
        kafka_producer,
        initial_delay=initial_delay,
        max_delay=max_delay,
        sleep_func=sleeps.append,
        ws_app_factory=factory,
    )
    return collector, factory, sleeps


# --------------------------------------------------------------------------- #
# Exponential backoff
# --------------------------------------------------------------------------- #
class TestExponentialBackoff(unittest.TestCase):
    def test_sequence_doubles_and_caps_at_max(self):
        backoff = ExponentialBackoff(initial_delay=5, max_delay=60)
        delays = [backoff.next_delay() for _ in range(6)]
        self.assertEqual(delays, [5, 10, 20, 40, 60, 60])

    def test_reset_returns_to_initial_delay(self):
        backoff = ExponentialBackoff(initial_delay=5, max_delay=60)
        backoff.next_delay()  # 5
        backoff.next_delay()  # 10
        self.assertEqual(backoff.current_delay, 20)
        backoff.reset()
        self.assertEqual(backoff.next_delay(), 5)

    def test_custom_initial_delay(self):
        backoff = ExponentialBackoff(initial_delay=2, max_delay=10)
        self.assertEqual(
            [backoff.next_delay() for _ in range(4)], [2, 4, 8, 10]
        )


# --------------------------------------------------------------------------- #
# Frame parsing / validation
# --------------------------------------------------------------------------- #
class TestParseTradeMessage(unittest.TestCase):
    def test_valid_trade(self):
        status, trade, reason = parse_trade_message(make_trade_message())
        self.assertEqual(status, MSG_TRADE)
        self.assertIsNone(reason)
        # The unified field structure sent to Kafka must remain unchanged.
        self.assertEqual(
            trade,
            {
                "trade_id": 12345,
                "symbol": "BTCUSDT",
                "price": "100.50",
                "quantity": "2.5",
                "trade_time": 1699999999999,
                "is_buyer_maker": True,
            },
        )
        self.assertEqual(
            set(trade.keys()),
            {
                "trade_id",
                "symbol",
                "price",
                "quantity",
                "trade_time",
                "is_buyer_maker",
            },
        )

    def test_bad_json_is_bad_frame(self):
        status, trade, reason = parse_trade_message(BAD_JSON_MESSAGE)
        self.assertEqual(status, MSG_BAD_FRAME)
        self.assertIsNone(trade)
        self.assertIn("JSON", reason)

    def test_non_object_json_is_bad_frame(self):
        for payload in ("[1, 2, 3]", "123", '"a string"', "null"):
            with self.subTest(payload=payload):
                status, _, _ = parse_trade_message(payload)
                self.assertEqual(status, MSG_BAD_FRAME)

    def test_non_trade_messages(self):
        for payload in (NON_TRADE_MESSAGE, SUBSCRIBE_ACK):
            with self.subTest(payload=payload):
                status, trade, reason = parse_trade_message(payload)
                self.assertEqual(status, MSG_NON_TRADE)
                self.assertIsNone(trade)
                self.assertIsNone(reason)

    def test_missing_each_required_field_is_bad_frame(self):
        raw = {
            "e": "trade",
            "s": "BTCUSDT",
            "t": 1,
            "p": "100.50",
            "q": "2.5",
            "T": 1699999999999,
            "m": True,
        }
        for raw_key in ("t", "s", "p", "q", "T", "m"):
            with self.subTest(missing=raw_key):
                broken = dict(raw)
                del broken[raw_key]
                status, trade, reason = parse_trade_message(json.dumps(broken))
                self.assertEqual(status, MSG_BAD_FRAME)
                self.assertIsNone(trade)
                self.assertIn("missing", reason)

    def test_wrong_types_are_bad_frames(self):
        base = {
            "e": "trade",
            "s": "BTCUSDT",
            "t": 1,
            "p": "100.50",
            "q": "2.5",
            "T": 1699999999999,
            "m": True,
        }
        bad_variants = {
            "t": "not-an-int",
            "s": 42,
            "p": 100.5,       # numeric instead of string
            "q": 2,           # numeric instead of string
            "T": "1699999999999",
            "m": "true",
        }
        for raw_key, bad_value in bad_variants.items():
            with self.subTest(field=raw_key):
                broken = dict(base)
                broken[raw_key] = bad_value
                status, trade, reason = parse_trade_message(json.dumps(broken))
                self.assertEqual(status, MSG_BAD_FRAME)
                self.assertIsNone(trade)
                self.assertIn("wrong type", reason)

    def test_boolean_is_not_accepted_as_integer(self):
        # bool is a subclass of int in Python; it must still be rejected.
        payload = json.dumps(
            {
                "e": "trade",
                "s": "BTCUSDT",
                "t": True,
                "p": "100.50",
                "q": "2.5",
                "T": 1699999999999,
                "m": True,
            }
        )
        status, _, reason = parse_trade_message(payload)
        self.assertEqual(status, MSG_BAD_FRAME)
        self.assertIn("wrong type", reason)

    def test_null_field_is_bad_frame(self):
        payload = json.dumps(
            {
                "e": "trade",
                "s": "BTCUSDT",
                "t": None,
                "p": "100.50",
                "q": "2.5",
                "T": 1699999999999,
                "m": True,
            }
        )
        status, _, _ = parse_trade_message(payload)
        self.assertEqual(status, MSG_BAD_FRAME)

    def test_parser_never_raises(self):
        # Even exotic inputs come back as a classified status, never an exception.
        for payload in ("", "   ", "\x00\x01", "{", "[]", "{}"):
            with self.subTest(payload=payload):
                status, _, _ = parse_trade_message(payload)
                self.assertIn(status, (MSG_TRADE, MSG_NON_TRADE, MSG_BAD_FRAME))


# --------------------------------------------------------------------------- #
# Collector callback behaviour on a single connection round
# --------------------------------------------------------------------------- #
class TestCollectorMessageHandling(unittest.TestCase):
    def _collector(self, kafka):
        return build_collector(kafka, scripts=[{"messages": []}])[0]

    def test_mixed_frames_counters_and_kafka_payload_structure(self):
        fake_kafka = FakeKafkaProducer()
        collector = self._collector(fake_kafka)

        app = object()
        collector.on_message(app, make_trade_message(trade_id=1))   # trade
        collector.on_message(app, BAD_JSON_MESSAGE)                 # bad frame
        collector.on_message(
            app, json.dumps({"e": "trade", "s": "X"})               # missing fields
        )
        collector.on_message(app, NON_TRADE_MESSAGE)                # non-trade
        collector.on_message(app, make_trade_message(trade_id=2))   # trade

        stats = collector.stats.as_dict()
        self.assertEqual(stats["messages_received"], 5)
        self.assertEqual(stats["trades_published"], 2)
        self.assertEqual(stats["bad_frames"], 2)
        self.assertEqual(stats["non_trade_messages"], 1)
        self.assertEqual(stats["publish_failures"], 0)
        # Every inbound frame is accounted for exactly once.
        self.assertEqual(
            stats["messages_received"],
            stats["trades_published"]
            + stats["bad_frames"]
            + stats["non_trade_messages"],
        )

        # Only valid trades were published, with the unchanged field structure.
        self.assertEqual(len(fake_kafka.sent), 2)
        for topic, value in fake_kafka.sent:
            self.assertEqual(topic, "raw_trades")
            self.assertEqual(
                set(value.keys()),
                {
                    "trade_id",
                    "symbol",
                    "price",
                    "quantity",
                    "trade_time",
                    "is_buyer_maker",
                },
            )
        self.assertEqual(fake_kafka.sent[0][1]["trade_id"], 1)
        self.assertEqual(fake_kafka.sent[1][1]["trade_id"], 2)

    def test_bad_frames_do_not_break_the_connection(self):
        # A whole round of garbage frames followed by a valid trade: the
        # connection keeps processing and the trade still gets published.
        fake_kafka = FakeKafkaProducer()
        collector = self._collector(fake_kafka)
        app = object()
        for _ in range(10):
            collector.on_message(app, BAD_JSON_MESSAGE)
        collector.on_message(app, make_trade_message(trade_id=77))

        self.assertEqual(collector.stats.messages_received, 11)
        self.assertEqual(collector.stats.bad_frames, 10)
        self.assertEqual(collector.stats.trades_published, 1)
        self.assertEqual(len(fake_kafka.sent), 1)

    def test_any_handled_message_resets_backoff(self):
        collector = self._collector(FakeKafkaProducer())
        collector.backoff.next_delay()
        collector.backoff.next_delay()
        self.assertEqual(collector.backoff.current_delay, 20)

        collector.on_message(object(), BAD_JSON_MESSAGE)
        self.assertEqual(collector.backoff.current_delay, 5)

        collector.backoff.next_delay()
        self.assertEqual(collector.backoff.current_delay, 10)
        collector.on_message(object(), NON_TRADE_MESSAGE)
        self.assertEqual(collector.backoff.current_delay, 5)

    def test_kafka_producer_none_keeps_parsing_alive(self):
        # Original behaviour preserved: log and skip, but never crash/block.
        collector = self._collector(None)
        collector.on_message(object(), make_trade_message())
        collector.on_message(object(), BAD_JSON_MESSAGE)
        collector.on_message(object(), NON_TRADE_MESSAGE)

        stats = collector.stats.as_dict()
        self.assertEqual(stats["messages_received"], 3)
        self.assertEqual(stats["trades_published"], 0)
        self.assertEqual(stats["publish_failures"], 1)
        self.assertEqual(stats["bad_frames"], 1)
        self.assertEqual(stats["non_trade_messages"], 1)

    def test_kafka_send_failure_does_not_block_later_messages(self):
        fake_kafka = FakeKafkaProducer(fail_send=True)
        collector = self._collector(fake_kafka)
        collector.on_message(object(), make_trade_message(trade_id=1))
        collector.on_message(object(), make_trade_message(trade_id=2))
        collector.on_message(object(), NON_TRADE_MESSAGE)

        stats = collector.stats.as_dict()
        self.assertEqual(stats["trades_published"], 0)
        self.assertEqual(stats["publish_failures"], 2)
        self.assertEqual(stats["non_trade_messages"], 1)
        self.assertEqual(stats["messages_received"], 3)

    def test_on_open_sends_subscription(self):
        collector = self._collector(FakeKafkaProducer())

        class _App:
            def __init__(self):
                self.sent = []

            def send(self, payload):
                self.sent.append(payload)

        app = _App()
        collector.on_open(app)
        self.assertEqual(len(app.sent), 1)
        sub = json.loads(app.sent[0])
        self.assertEqual(sub["method"], "SUBSCRIBE")
        self.assertEqual(sub["params"], ["btcusdt@trade"])


# --------------------------------------------------------------------------- #
# Reconnect loop: failures, settlement and recovery through the real callbacks
# --------------------------------------------------------------------------- #
class TestReconnectLoop(unittest.TestCase):
    def test_consecutive_failures_then_recovery_backoff_sequence(self):
        fake_kafka = FakeKafkaProducer()
        scripts = [
            {"raise": FakeConnectionError("exchange dropped us")},
            {"raise": FakeConnectionError("subscription rejected")},
            {
                "messages": [make_trade_message(trade_id=99)],
                "error": FakeConnectionError("mid-stream disconnect"),
                "close": True,
            },
            {"raise": FakeConnectionError("dropped again after recovery")},
        ]
        collector, factory, sleeps = build_collector(fake_kafka, scripts)

        collector.run_forever(max_rounds=4)

        # Backoff waits actually slept before each reconnect: 5s, 10s and then
        # 5s again because the healthy round 3 reset the backoff via on_message
        # (without the reset the 4th wait would have been 20s).
        self.assertEqual(sleeps, [5, 10, 5])

        history = collector.round_history
        self.assertEqual(len(history), 4)
        # Each round carries its own reconnect wait for settlement.
        self.assertEqual(
            [r["reconnect_wait_seconds"] for r in history], [0, 5, 10, 5]
        )
        self.assertEqual(history[0]["messages_received"], 0)
        self.assertEqual(history[1]["messages_received"], 0)
        self.assertEqual(history[2]["messages_received"], 1)
        self.assertEqual(history[2]["trades_published"], 1)
        self.assertEqual(history[2]["bad_frames"], 0)
        self.assertEqual(history[2]["non_trade_messages"], 0)

        self.assertEqual(len(fake_kafka.sent), 1)
        self.assertEqual(fake_kafka.sent[0][1]["trade_id"], 99)
        self.assertEqual(len(factory.apps), 4)

    def test_backoff_caps_at_sixty_seconds(self):
        scripts = [
            {"raise": FakeConnectionError("down")} for _ in range(6)
        ]
        collector, _, sleeps = build_collector(FakeKafkaProducer(), scripts)

        collector.run_forever(max_rounds=6)

        self.assertEqual(sleeps, [5, 10, 20, 40, 60])
        self.assertEqual(
            [r["reconnect_wait_seconds"] for r in collector.round_history],
            [0, 5, 10, 20, 40, 60],
        )

    def test_successful_round_resets_grown_backoff(self):
        scripts = [
            {"raise": FakeConnectionError("down")},                 # r1 fail
            {"messages": [make_trade_message()], "close": True},    # r2 healthy
            {"raise": FakeConnectionError("down again")},           # r3 fail
        ]
        collector, _, sleeps = build_collector(FakeKafkaProducer(), scripts)

        collector.run_forever(max_rounds=3)

        # r2 handled a message -> backoff reset -> r3 waits 5s again, not 10s.
        self.assertEqual(sleeps, [5, 5])
        self.assertEqual(
            [r["reconnect_wait_seconds"] for r in collector.round_history],
            [0, 5, 5],
        )

    def test_bad_frame_only_round_also_resets_backoff(self):
        # Any inbound frame proves the transport is alive.
        scripts = [
            {"raise": FakeConnectionError("down")},
            {"raise": FakeConnectionError("down")},
            {"messages": [BAD_JSON_MESSAGE, BAD_JSON_MESSAGE], "close": True},
            {"raise": FakeConnectionError("down")},
        ]
        collector, _, sleeps = build_collector(FakeKafkaProducer(), scripts)

        collector.run_forever(max_rounds=4)

        # Without the reset the fourth wait would have been 20s.
        self.assertEqual(sleeps, [5, 10, 5])
        self.assertEqual(collector.round_history[2]["bad_frames"], 2)

    def test_full_scenario_settlement_numbers(self):
        # One round containing every frame type, driven via run_forever.
        scripts = [
            {
                "messages": [
                    make_trade_message(trade_id=1),
                    BAD_JSON_MESSAGE,
                    json.dumps({"e": "trade", "s": "BTCUSDT"}),  # missing fields
                    NON_TRADE_MESSAGE,
                    SUBSCRIBE_ACK,
                    make_trade_message(trade_id=2),
                ],
                "close": True,
            }
        ]
        fake_kafka = FakeKafkaProducer()
        collector, factory, sleeps = build_collector(fake_kafka, scripts)

        with self.assertLogs(level=logging.INFO) as logs:
            collector.run_forever(max_rounds=1)

        settlement = collector.round_history[0]
        self.assertEqual(settlement["messages_received"], 6)
        self.assertEqual(settlement["trades_published"], 2)
        self.assertEqual(settlement["bad_frames"], 2)
        self.assertEqual(settlement["non_trade_messages"], 2)
        self.assertEqual(settlement["reconnect_wait_seconds"], 0)
        self.assertEqual(sleeps, [])  # first connection attempts immediately

        # A human/greppable settlement line was emitted with all required fields.
        settlement_lines = [
            line for line in logs.output if "ROUND SETTLEMENT" in line
        ]
        self.assertEqual(len(settlement_lines), 1)
        line = settlement_lines[0]
        for token in (
            "messages_received=6",
            "trades_published=2",
            "bad_frames=2",
            "non_trade_messages=2",
            "reconnect_wait_seconds=0",
        ):
            self.assertIn(token, line)

        # The subscription was sent through the real on_open callback.
        self.assertEqual(len(factory.apps[0].subscriptions), 1)
        self.assertEqual(len(fake_kafka.sent), 2)

    def test_run_forever_exception_is_swallowed_and_loop_continues(self):
        scripts = [
            {"raise": FakeConnectionError("fatal? no.")},
            {"messages": [make_trade_message()], "close": True},
        ]
        collector, _, sleeps = build_collector(FakeKafkaProducer(), scripts)
        # Must not raise; the reconnect loop survives the run_forever exception.
        collector.run_forever(max_rounds=2)
        self.assertEqual(sleeps, [5])
        self.assertEqual(collector.round_history[1]["trades_published"], 1)

    def test_run_producer_returns_collector_and_drives_loop(self):
        fake_kafka = FakeKafkaProducer()
        factory = FakeWSFactory(
            [{"messages": [make_trade_message(trade_id=5)], "close": True}]
        )
        sleeps = []
        collector = run_producer(
            kafka_producer=fake_kafka,
            sleep_func=sleeps.append,
            ws_app_factory=factory,
            max_rounds=1,
        )
        self.assertIsInstance(collector, TradeStreamCollector)
        self.assertEqual(collector.round_history[0]["trades_published"], 1)
        self.assertEqual(fake_kafka.sent[0][1]["trade_id"], 5)

    def test_run_producer_exits_early_when_kafka_unavailable_at_startup(self):
        original = producer_mod.create_kafka_producer
        producer_mod.create_kafka_producer = lambda *args, **kwargs: None
        try:
            # Must return without attempting any WebSocket connection or sleep.
            sleeps = []
            run_producer(sleep_func=sleeps.append)
            self.assertEqual(sleeps, [])
        finally:
            producer_mod.create_kafka_producer = original

    def test_round_stats_resets_between_rounds(self):
        scripts = [
            {"messages": [make_trade_message(), BAD_JSON_MESSAGE], "close": True},
            {"messages": [NON_TRADE_MESSAGE], "close": True},
        ]
        collector, _, _ = build_collector(FakeKafkaProducer(), scripts)
        collector.run_forever(max_rounds=2)
        r1, r2 = collector.round_history
        self.assertEqual(r1["messages_received"], 2)
        self.assertEqual(r1["trades_published"], 1)
        self.assertEqual(r1["bad_frames"], 1)
        self.assertEqual(r2["messages_received"], 1)
        self.assertEqual(r2["non_trade_messages"], 1)
        self.assertEqual(r2["trades_published"], 0)
        self.assertEqual(r2["bad_frames"], 0)


if __name__ == "__main__":
    unittest.main()
