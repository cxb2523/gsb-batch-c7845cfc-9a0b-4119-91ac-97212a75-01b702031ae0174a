# 49. Beam Advanced Patterns

> Production-grade Apache Beam patterns: tagged side outputs for dead-letter routing and event-time windowing for time-series aggregation. **GCP Services:** (optional) Cloud Dataflow, Pub/Sub, BigQuery

## Architecture

```
Pattern 1: Branching + Dead-Letter
─────────────────────────────────
data/sales_data.csv
        │
  ReadFromText
        │
  ParDo(ValidateRow)
  .with_outputs("invalid", main="valid")
        │
        ├── valid rows ──────────────────► BigQuery / destination
        │   (untagged yield)
        │
        └── invalid rows (TaggedOutput) ──► Dead-letter sink (BQ table / GCS)
            - parse errors                   + Cloud Monitoring alert on rate
            - quantity <= 0
            - total != qty * unit_price


Pattern 2: Event-Time Windowing
────────────────────────────────
data/events.json (20 events, Jan–May 2026)
        │
  beam.Create(events)
        │
  Map(to_timestamped)          ← assigns event_timestamp as Beam event time
        │
  WindowInto(FixedWindows(30d))
        │
  CombineGlobally(sum).without_defaults()
        │
  ParDo(FormatWindowedSum)     ← injects WindowParam for boundary dates
        │
  Print: window [start → end)  revenue = $X

  Streaming upgrade: swap beam.Create → ReadFromPubSub(timestamp_attribute=...)
```

## What this demonstrates

- `TaggedOutput` + `with_outputs`: branching a single PCollection into N streams
- Dead-letter pattern: bad rows routed aside instead of crashing/stalling the pipeline
- Event-time windowing: `FixedWindows`, `TimestampedValue`, `DoFn.WindowParam`
- `CombineGlobally(sum).without_defaults()`: required for windowed aggregation
- How to map a bounded (batch) windowing demo 1:1 to unbounded streaming (Pub/Sub)
- Why streaming Dataflow costs money even at zero traffic (always-on workers)

## Prerequisites

No GCP project required for DirectRunner. If using DataflowRunner:

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
gcloud auth application-default login
```

## Setup

```bash
# DirectRunner: no setup needed — skip straight to Run
chmod +x deploy.sh && ./deploy.sh   # optional: enables APIs for Dataflow path
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Expected output excerpt:
```
[1/2] Branching with Tagged Side Outputs
  valid rows   : 20
  invalid rows : 0
  No dead-letter rows — all data passed validation.

[2/2] Event-Time Windowing
  window [2026-01-01 → 2026-01-31)  revenue = $  5,161.24
  window [2026-01-31 → 2026-03-01)  revenue = $  6,770.24
  window [2026-03-01 → 2026-03-31)  revenue = $  6,235.50
  window [2026-03-31 → 2026-04-30)  revenue = $  5,480.00
  window [2026-04-30 → 2026-05-30)  revenue = $  1,940.00
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator — runs branching then windowing demos |
| `branching.py` | Dead-letter pattern: `ValidateRow` DoFn with TaggedOutput side outputs |
| `windowing.py` | Event-time windowing: `FixedWindows`, `TimestampedValue`, `FormatWindowedSum` |
| `data/sales_data.csv` | 20-row sales fixture for branching demo |
| `data/events.json` | 20 event records with ISO timestamps for windowing demo |
| `deploy.sh` | DirectRunner needs no setup; documents DataflowRunner + Pub/Sub path |
| `requirements.txt` | Python dependencies |

## Dead-letter pattern in production

```
Invalid rows → WriteToBigQuery(dead_letter_table, write_disposition=WRITE_APPEND)
             → Cloud Monitoring metric on table row count
             → PagerDuty/Slack alert when rate spikes
             → Replay: fix root cause, reprocess from dead-letter table
```

Never drop bad rows silently — one poison message on a streaming pipeline retries forever.

## Event-time windowing → streaming upgrade

```python
# Swap this (batch):
pipeline | beam.Create(events) | beam.Map(to_timestamped)

# For this (streaming):
pipeline | beam.io.ReadFromPubSub(
    subscription="projects/your-project-id/subscriptions/events-sub",
    timestamp_attribute="event_time",   # message attribute carries event time
)
# No to_timestamped needed — Beam reads the timestamp from the attribute.
# Add streaming=True to PipelineOptions and run on DataflowRunner.
```

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
