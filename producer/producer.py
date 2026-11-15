import websocket
import json
import os
import logging
import time
from typing import Any, Callable, Optional

from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

EXCHANGE_WS_URL = os.getenv("EXCHANGE_WS_URL", "wss://stream.binance.com:9443/ws")
CRYPTO_PAIR = os.getenv("CRYPTO_PAIR", "btcusdt")
KAFKA_BROKER_URL = os.getenv("KAFKA_BROKER_URL", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "raw_trades")

# Exponential backoff settings for reconnecting after a dropped connection.
RECONNECT_INITIAL_DELAY = 5
RECONNECT_MAX_DELAY = 60

# Outcome of classifying one inbound WebSocket message.
MSG_TRADE = "trade"
MSG_NON_TRADE = "non_trade"
MSG_BAD_FRAME = "bad_frame"

# Binance raw field -> unified Kafka field, with the accepted Python type(s).
# The unified field names/structure must stay identical to the previous version.
REQUIRED_FIELDS = (
    ("t", "trade_id", (int,)),
    ("s", "symbol", (str,)),
    ("p", "price", (str,)),
    ("q", "quantity", (str,)),
    ("T", "trade_time", (int,)),
    ("m", "is_buyer_maker", (bool,)),
)


def create_kafka_producer(retries: int = 5, delay: int = 5) -> Optional[KafkaProducer]:
    for retry in range(retries):
        try:
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_BROKER_URL,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
            logging.info("Successfully connected to Kafka broker.")
            return producer
        except NoBrokersAvailable:
            logging.error(
                f"Kafka broker not available. Retrying in {delay} seconds... ({retry+1}/{retries})"
            )
            time.sleep(delay)
    logging.critical(
        "Could not connect to Kafka broker after several retries. Exiting."
    )
    return None


class ExponentialBackoff:
    """Exponential reconnect delay: 5s -> 10s -> 20s -> 40s -> 60s (capped)."""

    def __init__(
        self, initial_delay: int = RECONNECT_INITIAL_DELAY,
        max_delay: int = RECONNECT_MAX_DELAY,
    ) -> None:
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.current_delay = initial_delay

    def next_delay(self) -> int:
        """Return the delay to wait before the next reconnect and grow the backoff."""
        delay = self.current_delay
        self.current_delay = min(self.current_delay * 2, self.max_delay)
        return delay

    def reset(self) -> None:
        """Go back to the initial delay after a message is successfully handled."""
        self.current_delay = self.initial_delay


class RoundStats:
    """Per-connection counters, reset at the start of every new connection."""

    def __init__(self) -> None:
        self.messages_received = 0
        self.trades_published = 0
        self.bad_frames = 0
        self.non_trade_messages = 0
        self.publish_failures = 0
        self.reconnect_wait_seconds = 0

    def record_message(self) -> None:
        self.messages_received += 1

    def record_published(self) -> None:
        self.trades_published += 1

    def record_bad_frame(self) -> None:
        self.bad_frames += 1

    def record_non_trade(self) -> None:
        self.non_trade_messages += 1

    def record_publish_failure(self) -> None:
        self.publish_failures += 1

    def as_dict(self) -> dict:
        return {
            "messages_received": self.messages_received,
            "trades_published": self.trades_published,
            "bad_frames": self.bad_frames,
            "non_trade_messages": self.non_trade_messages,
            "publish_failures": self.publish_failures,
            "reconnect_wait_seconds": self.reconnect_wait_seconds,
        }

    def log_settlement(self, round_number: int) -> None:
        logging.info(
            "ROUND SETTLEMENT round=%s messages_received=%s trades_published=%s "
            "bad_frames=%s non_trade_messages=%s publish_failures=%s "
            "reconnect_wait_seconds=%s",
            round_number,
            self.messages_received,
            self.trades_published,
            self.bad_frames,
            self.non_trade_messages,
            self.publish_failures,
            self.reconnect_wait_seconds,
        )


def parse_trade_message(message: str) -> tuple[str, Optional[dict], Optional[str]]:
    """
    Parse and validate one raw WebSocket frame.

    Returns (status, formatted_trade, reason):
      * (MSG_TRADE, trade_dict, None)        - a valid trade ready for Kafka
      * (MSG_NON_TRADE, None, None)          - valid JSON but not a trade event
      * (MSG_BAD_FRAME, None, reason)        - unparseable / incomplete / wrong type

    Never raises: any malformed frame becomes MSG_BAD_FRAME so that a single
    bad message cannot tear down the whole connection.
    """
    try:
        raw_data = json.loads(message)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return MSG_BAD_FRAME, None, f"JSON decode error: {exc}"

    if not isinstance(raw_data, dict):
        return MSG_BAD_FRAME, None, "message body is not a JSON object"

    if raw_data.get("e") != "trade":
        return MSG_NON_TRADE, None, None

    formatted_trade: dict[str, Any] = {}
    for raw_key, target_key, expected_types in REQUIRED_FIELDS:
        if raw_key not in raw_data:
            return (
                MSG_BAD_FRAME,
                None,
                f"missing required field '{target_key}' (raw key '{raw_key}')",
            )
        value = raw_data[raw_key]
        # bool is a subclass of int; reject booleans where an integer is expected.
        if isinstance(value, bool) and int in expected_types and bool not in expected_types:
            return (
                MSG_BAD_FRAME,
                None,
                f"field '{target_key}' has wrong type: bool",
            )
        if not isinstance(value, expected_types) or value is None:
            return (
                MSG_BAD_FRAME,
                None,
                f"field '{target_key}' has wrong type: {type(value).__name__}",
            )
        formatted_trade[target_key] = value

    return MSG_TRADE, formatted_trade, None


class TradeStreamCollector:
    """
    Owns the reconnect loop, the per-round counters and the WebSocket callbacks.

    The callbacks (on_open/on_message/on_error/on_close) are the real handlers
    handed to websocket.WebSocketApp; the parser/backoff/stats collaborators are
    injected so the full callback path can be driven with fakes in unit tests.
    """

    def __init__(
        self,
        kafka_producer: Optional[KafkaProducer],
        pair: str = CRYPTO_PAIR,
        topic: str = KAFKA_TOPIC,
        initial_delay: int = RECONNECT_INITIAL_DELAY,
        max_delay: int = RECONNECT_MAX_DELAY,
        sleep_func: Callable[[float], None] = time.sleep,
        ws_app_factory: Callable[..., websocket.WebSocketApp] = websocket.WebSocketApp,
    ) -> None:
        self.kafka_producer = kafka_producer
        self.pair = pair
        self.topic = topic
        self.sleep_func = sleep_func
        self.ws_app_factory = ws_app_factory
        self.backoff = ExponentialBackoff(initial_delay, max_delay)
        self.stats = RoundStats()
        # Settlement snapshot of every connection round (oldest first).
        self.round_history: list[dict] = []

    # ------------------------------------------------------------------ #
    # Real websocket-client callback path
    # ------------------------------------------------------------------ #
    def on_open(self, ws: websocket.WebSocketApp) -> None:
        subscription_message = {
            "method": "SUBSCRIBE",
            "params": [f"{self.pair}@trade"],
            "id": 1,
        }
        ws.send(json.dumps(subscription_message))
        logging.info(f"Subscribed to {self.pair} trade stream.")

    def on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logging.error(f"WebSocket Error: {error}")

    def on_close(
        self, ws: websocket.WebSocketApp, close_status_code: int, close_msg: str
    ) -> None:
        logging.warning(
            f"WebSocket connection closed. Status: {close_status_code}, Msg: {close_msg}. "
            "Will reconnect with exponential backoff."
        )

    def on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        self.stats.record_message()
        try:
            status, formatted_trade, reason = parse_trade_message(message)

            if status == MSG_BAD_FRAME:
                self.stats.record_bad_frame()
                logging.error(
                    "Skipping bad frame (bad_frames=%s): %s; raw message: %s",
                    self.stats.bad_frames,
                    reason,
                    message,
                )
            elif status == MSG_NON_TRADE:
                self.stats.record_non_trade()
                logging.info("Received non-trade message: %s", message)
            elif self._publish(formatted_trade):
                self.stats.record_published()
            else:
                self.stats.record_publish_failure()
        except Exception as exc:
            # Absolute safety net: a faulty frame must never kill the connection.
            self.stats.record_bad_frame()
            logging.exception("Unexpected error while handling frame, skipping: %s", exc)
        finally:
            # Receiving any frame proves the transport is alive, so a healthy
            # connection (even one carrying only bad/non-trade frames) restarts
            # the reconnect backoff at the initial value.
            self.backoff.reset()

    def _publish(self, formatted_trade: dict) -> bool:
        """Send one trade to Kafka. Returns False when Kafka is unavailable."""
        if not self.kafka_producer:
            logging.error("Kafka producer is not available. Cannot process message.")
            return False
        try:
            self.kafka_producer.send(self.topic, value=formatted_trade)
            logging.info(f"Published trade to Kafka: {formatted_trade['trade_id']}")
            return True
        except Exception as exc:
            # Kafka hiccups must not block or crash message parsing.
            logging.error(
                "Failed to publish trade %s to Kafka: %s",
                formatted_trade.get("trade_id"),
                exc,
            )
            return False

    # ------------------------------------------------------------------ #
    # Reconnect loop
    # ------------------------------------------------------------------ #
    def run_forever(self, max_rounds: Optional[int] = None) -> None:
        ws_url = f"{EXCHANGE_WS_URL}/{self.pair}@trade"
        round_number = 0
        # No wait before the very first connection attempt.
        wait_seconds = 0

        while max_rounds is None or round_number < max_rounds:
            round_number += 1
            self.stats = RoundStats()
            self.stats.reconnect_wait_seconds = wait_seconds

            if wait_seconds > 0:
                logging.info(f"Reconnecting in {wait_seconds} seconds...")
                self.sleep_func(wait_seconds)

            logging.info(f"Connecting to WebSocket URL: {ws_url}")
            try:
                ws = self.ws_app_factory(
                    ws_url,
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close,
                )
                ws.run_forever()
            except Exception as exc:
                logging.error(f"WebSocketApp run_forever failed with error: {exc}")

            self.stats.log_settlement(round_number)
            self.round_history.append(self.stats.as_dict())

            # Compute the delay for the next reconnect; reset() during the
            # round (on any inbound message) collapses this back to 5s.
            wait_seconds = self.backoff.next_delay()


def run_producer(
    kafka_producer: Optional[KafkaProducer] = None,
    retry_delay: int = RECONNECT_INITIAL_DELAY,
    max_delay: int = RECONNECT_MAX_DELAY,
    sleep_func: Callable[[float], None] = time.sleep,
    ws_app_factory: Optional[Callable[..., websocket.WebSocketApp]] = None,
    max_rounds: Optional[int] = None,
) -> None:
    if kafka_producer is None:
        kafka_producer = create_kafka_producer()
    if not kafka_producer:
        return

    collector = TradeStreamCollector(
        kafka_producer,
        initial_delay=retry_delay,
        max_delay=max_delay,
        sleep_func=sleep_func,
        ws_app_factory=ws_app_factory or websocket.WebSocketApp,
    )
    collector.run_forever(max_rounds=max_rounds)
    return collector


def main() -> None:
    kafka_producer = create_kafka_producer()
    if not kafka_producer:
        return
    run_producer(kafka_producer)


if __name__ == "__main__":
    main()
