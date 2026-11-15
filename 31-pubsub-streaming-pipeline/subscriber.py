# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Pub/Sub Subscriber — streaming pull with ack.
Gets-or-creates a pull subscription, consumes messages via streaming_pull_future
for a fixed time window, and acks them. Covers at-least-once semantics and
ack deadlines.
"""

import json
import os
import threading

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
TOPIC_ID = os.environ.get("PUBSUB_TOPIC_ID", "gcp-hub-demo-topic")
SUBSCRIPTION_ID = os.environ.get("PUBSUB_SUBSCRIPTION_ID", "gcp-hub-demo-topic-pull")
PULL_TIMEOUT_SECONDS = 15  # demo window; real workers run until SIGTERM


def get_or_create_subscription(subscriber, topic_path, sub_path):
    """Creates the pull subscription if it does not already exist."""
    try:
        subscriber.create_subscription(
            request={
                "name": sub_path,
                "topic": topic_path,
                # Ack deadline = how long Pub/Sub waits for your ack before
                # redelivering. The client lib auto-extends it while a message
                # is in flight, but size it to your real processing time —
                # too short causes duplicate storms under load.
                "ack_deadline_seconds": 30,
            }
        )
        print(f"  created subscription : {sub_path}")
        print("  NOTE: a subscription only receives messages published AFTER "
              "it exists — republish if this pull is empty.")
    except AlreadyExists:
        print(f"  subscription exists  : {sub_path}")


def pull_messages(
    project_id=PROJECT_ID,
    topic_id=TOPIC_ID,
    subscription_id=SUBSCRIPTION_ID,
    timeout=PULL_TIMEOUT_SECONDS,
):
    """
    Opens a streaming pull on the subscription for `timeout` seconds.
    Acks every received message and returns the list of decoded payloads.

    Args:
        project_id:      GCP project ID.
        topic_id:        Pub/Sub topic ID (used to create the subscription).
        subscription_id: Pub/Sub subscription ID.
        timeout:         Seconds to listen before closing the stream.

    Returns:
        list[dict]: decoded message payloads received during the window.
    """
    subscriber = pubsub_v1.SubscriberClient()
    topic_path = subscriber.topic_path(project_id, topic_id)
    sub_path = subscriber.subscription_path(project_id, subscription_id)
    get_or_create_subscription(subscriber, topic_path, sub_path)

    received = []
    lock = threading.Lock()

    def callback(message):
        # AT-LEAST-ONCE: Pub/Sub guarantees delivery >= 1 time, never exactly
        # once (unless you opt into exactly-once subscriptions, which cost
        # throughput). Design handlers to be IDEMPOTENT — e.g. key writes on
        # message_id or a business key (order_id) so a redelivery is a no-op.
        payload = json.loads(message.data.decode("utf-8"))
        with lock:
            received.append(payload)
        print(
            f"    got message_id={message.message_id} "
            f"order_id={payload.get('order_id')} "
            f"attrs={dict(message.attributes)}"
        )
        # Ack ONLY after the work is durably done. Ack-then-process means a
        # crash mid-handler silently drops the message. nack() on failure to
        # trigger redelivery (pair with a dead-letter topic for poison pills).
        message.ack()

    # Streaming pull holds a gRPC stream open and pushes messages into a
    # thread pool — far lower latency than synchronous pull() polling.
    # Use flow_control to bound memory on bursty topics.
    flow_control = pubsub_v1.types.FlowControl(max_messages=50)
    streaming_pull_future = subscriber.subscribe(
        sub_path, callback=callback, flow_control=flow_control
    )
    print(f"\n  listening on '{subscription_id}' for {timeout}s...\n")

    with subscriber:
        try:
            # Block the main thread; TimeoutError ends the demo window.
            streaming_pull_future.result(timeout=timeout)
        except TimeoutError:
            streaming_pull_future.cancel()   # stop accepting new messages
            streaming_pull_future.result()   # drain in-flight callbacks

    print(f"\n  OK: received and acked {len(received)} messages.")
    if not received:
        print("  (empty pull — publish first with publisher.py)")
    return received
