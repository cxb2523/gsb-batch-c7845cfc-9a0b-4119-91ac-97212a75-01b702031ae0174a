# 31. Pub/Sub Streaming Pipeline

> End-to-end Pub/Sub producer → consumer demo: publish JSON event records with message attributes, pull them back with streaming pull, and ack them. **GCP Services:** Cloud Pub/Sub

## Architecture

```
data/messages.json
       │
       ▼
  publisher.py  ──► Pub/Sub Topic (gcp-hub-demo-topic)
  (batch publish,                       │
   attributes,                          ▼
   futures)              Subscription (pull, ack-deadline 30s)
                                        │
                                        ▼
                         subscriber.py (streaming pull,
                                        flow control,
                                        ack-on-durable-write)
                                        │
                                        ▼
                              Console print + summary
```

## What this demonstrates

- Creating Pub/Sub topics and pull subscriptions idempotently (AlreadyExists guard)
- Client-side batching configuration (`BatchSettings`) for throughput vs latency trade-offs
- Async `publish()` futures — resolving them to catch silent data loss
- Message attributes for server-side subscription filter routing (event_type, region, schema_version)
- Streaming pull (`subscribe()`) with `FlowControl` to bound memory on bursty topics
- At-least-once delivery semantics: ack-after-durable-write pattern, not ack-then-process
- Threading-safe message collection with a lock inside the callback

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
# Optional overrides
export PUBSUB_TOPIC_ID=gcp-hub-demo-topic
export PUBSUB_SUBSCRIPTION_ID=gcp-hub-demo-topic-pull
```

Authenticate:

```bash
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

The script enables `pubsub.googleapis.com` and creates the topic + subscription.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

`main.py` publishes all 10 records from `data/messages.json`, then opens a 15-second streaming pull window to receive and ack them.

You can also run publisher and subscriber independently:

```bash
python publisher.py   # publish only
python subscriber.py  # subscribe only (15s window)
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrates publish → subscribe in sequence |
| `publisher.py` | Pub/Sub publish: topic creation, batching, futures, attributes |
| `subscriber.py` | Streaming pull: subscription creation, flow control, ack |
| `data/messages.json` | 10 sample order event records used as message payloads |
| `deploy.sh` | Enables API, creates topic and subscription |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
