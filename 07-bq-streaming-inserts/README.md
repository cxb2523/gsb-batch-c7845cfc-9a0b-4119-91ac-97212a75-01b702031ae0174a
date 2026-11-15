# 7. BigQuery Streaming Inserts

> Stream event rows into BigQuery in real time using the legacy `insert_rows_json` API, with best-effort deduplication and immediate queryability. **GCP Services:** BigQuery

## Architecture

```
Synthetic Events           data/sales_data.csv
      │                           │
      ▼                           ▼
  producer.py                producer.py
  stream_batch()           stream_from_csv()
      │                           │
      └──────────┬────────────────┘
                 │ insert_rows_json
                 │ (row_ids = event_id for dedup)
                 ▼
     ┌─────────────────────────┐
     │  BigQuery               │
     │  Dataset: your_dataset  │
     │  Table: events_stream   │
     │  (streaming buffer)     │
     └─────────────────────────┘
                 │ immediately queryable
                 ▼
          SELECT ... results
```

## What this demonstrates

- Creating a BigQuery dataset and table with explicit schema and labels
- Streaming JSON rows with `insert_rows_json` and per-row `row_ids` for best-effort dedup
- Multi-batch streaming: synthetic events + CSV rows bridged into the streaming path
- Querying streamed rows immediately (buffer latency vs `num_rows` metadata lag)
- API trade-off commentary: legacy streaming vs Load jobs vs Storage Write API

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export BQ_DATASET=your_dataset    # optional, defaults to your_dataset
```

Authenticate:
```bash
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator: dataset → table → stream batches → query |
| `schema.py` | `ensure_dataset()` and `ensure_events_table()` with explicit schema |
| `producer.py` | `stream_batch()` (synthetic events) and `stream_from_csv()` |
| `data/sales_data.csv` | Sample GCP product order data used as CSV import demo |
| `deploy.sh` | API enablement |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
