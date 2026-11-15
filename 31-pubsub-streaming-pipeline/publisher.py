# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Pub/Sub Publisher — topics, attributes, futures, batching.
Gets-or-creates a Pub/Sub topic and publishes JSON messages with attributes.
Demonstrates the future-based async publish flow and client-side batching.
"""

import json
import os
from pathlib import Path

import pandas as pd
from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
TOPIC_ID = os.environ.get("PUBSUB_TOPIC_ID", "gcp-hub-demo-topic")

MESSAGES_PATH = Path(__file__).resolve().parent / "data" / "messages.json"

# Batching: the client buffers messages and ships them in one RPC when ANY
# threshold trips. Defaults (100 msgs / 1 MB / 10 ms) suit high throughput.
# Tuning guide:
#  * latency-sensitive (user-facing events): drop max_latency to ~0.01s
#    — you trade RPC efficiency for delivery speed.
#  * bulk backfill: raise max_messages/max_bytes — fewer RPCs, cheaper CPU.
BATCH_SETTINGS = pubsub_v1.types.BatchSettings(
    max_messages=100,
    max_bytes=1024 * 1024,
    max_latency=0.05,  # seconds the client will wait to fill a batch
)


def get_or_create_topic(publisher, topic_path):
    """Creates the Pub/Sub topic if it does not already exist."""
    try:
        publisher.create_topic(request={"name": topic_path})
        print(f"  created topic : {topic_path}")
    except AlreadyExists:
        print(f"  topic exists  : {topic_path}")
    return topic_path


def load_messages(path=MESSAGES_PATH):
    """Loads sample messages from the JSON fixture file."""
    with open(path) as f:
        return json.load(f)


def publish_messages(project_id=PROJECT_ID, topic_id=TOPIC_ID, messages=None):
    """
    Publishes a list of event dicts to the given Pub/Sub topic.
    Returns the number of messages successfully published.

    Args:
        project_id: GCP project ID.
        topic_id:   Pub/Sub topic ID.
        messages:   List of dicts. Loaded from data/messages.json if None.

    Returns:
        int: count of published messages.
    """
    if messages is None:
        messages = load_messages()

    publisher = pubsub_v1.PublisherClient(batch_settings=BATCH_SETTINGS)
    topic_path = publisher.topic_path(project_id, topic_id)
    get_or_create_topic(publisher, topic_path)

    print(f"\n  publishing {len(messages)} messages to {topic_id}...\n")

    futures = []
    for rec in messages:
        data = json.dumps(rec).encode("utf-8")  # payload must be bytes

        # Attributes ride OUTSIDE the payload — subscribers can filter on
        # them server-side (subscription filters), so put routing keys here
        # (event type, region, schema version) and keep the body opaque.
        future = publisher.publish(
            topic_path,
            data,
            event_type=rec.get("event_type", "order.created"),
            region=str(rec.get("region", "unknown")),
            schema_version="1",
        )
        futures.append((rec.get("order_id", "?"), future))

    # publish() is async — it returns a future immediately while the batch
    # buffer fills. Always resolve futures (or attach callbacks): an
    # unobserved failed publish is silent data loss.
    published = 0
    for order_id, future in futures:
        message_id = future.result(timeout=30)  # raises if publish failed
        print(f"    order_id={order_id} -> message_id={message_id}")
        published += 1

    print(f"\n  OK: {published} messages published to topic '{topic_id}'.")
    print(f"  Run subscriber.py to pull them back.\n")
    return published
