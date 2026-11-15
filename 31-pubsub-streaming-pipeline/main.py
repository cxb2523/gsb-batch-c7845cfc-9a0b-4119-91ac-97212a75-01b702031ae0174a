# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Pub/Sub Streaming Pipeline — full cycle demo.
Runs the publisher then the subscriber in sequence to demonstrate:
  1. Topic and subscription creation (idempotent)
  2. Async batch publish with futures
  3. Streaming pull with ack and at-least-once semantics
"""

import os

from publisher import publish_messages, load_messages
from subscriber import pull_messages

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
TOPIC_ID = os.environ.get("PUBSUB_TOPIC_ID", "gcp-hub-demo-topic")
SUBSCRIPTION_ID = os.environ.get("PUBSUB_SUBSCRIPTION_ID", "gcp-hub-demo-topic-pull")


def main():
    print("=" * 65)
    print("  GCP DATA ENGINEERING HUB — Project 31")
    print("  Pub/Sub Streaming Pipeline")
    print("=" * 65)

    # ── Step 1: Publish ──────────────────────────────────────────────
    print("\n[STEP 1] PUBLISH — loading messages from data/messages.json")
    messages = load_messages()
    published = publish_messages(
        project_id=PROJECT_ID,
        topic_id=TOPIC_ID,
        messages=messages,
    )

    # ── Step 2: Subscribe ────────────────────────────────────────────
    print("[STEP 2] SUBSCRIBE — opening streaming pull (15s window)")
    received = pull_messages(
        project_id=PROJECT_ID,
        topic_id=TOPIC_ID,
        subscription_id=SUBSCRIPTION_ID,
    )

    # ── Summary ──────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print(f"  Published : {published} messages")
    print(f"  Received  : {len(received)} messages")
    print("=" * 65)


if __name__ == "__main__":
    main()
