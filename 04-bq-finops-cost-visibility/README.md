# 4. BigQuery FinOps & Cost Visibility

> Pre-execution cost estimation via dry runs, live spend auditing via INFORMATION_SCHEMA, and scheduled query automation with the Data Transfer Service. **GCP Services:** BigQuery, BigQuery Data Transfer Service

## Architecture

```
Any SQL Query ──► dry_run=True ──► bytes_processed (free, no slots) ──► $ Estimate

Project INFORMATION_SCHEMA.JOBS_BY_PROJECT ──► Top 10 queries by bytes billed
                                            ──► Per-user spend / slot-hours (7-day window)

Scheduled SQL ──► Data Transfer Service ──► Every 24h ──► daily_word_summary table
```

## What this demonstrates

- `QueryJobConfig(dry_run=True, use_query_cache=False)` to estimate bytes scanned before execution (free)
- Translating bytes to on-demand cost at $6.25/TiB list price
- `maximum_bytes_billed` guardrail pattern to fail-fast on runaway queries
- `INFORMATION_SCHEMA.JOBS_BY_PROJECT` for top-N queries by bytes billed and per-user spend
- `total_slot_ms` → slot-hours conversion for BigQuery Editions (capacity) pricing
- COALESCE handling for cached/metadata-only jobs with NULL bytes/slots
- Creating a scheduled query via `bigquery_datatransfer.DataTransferServiceClient`
- DTS `data_source_id="scheduled_query"` with `CREATE OR REPLACE TABLE` DDL as the payload

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id   # ← replace
export GOOGLE_CLOUD_LOCATION=us-central1
export BQ_DATASET=your_dataset                # ← replace
gcloud auth application-default login

# The INFORMATION_SCHEMA queries require:
# roles/bigquery.resourceViewer  OR  bigquery.jobs.listAll on the project
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
| `main.py` | Entry point — runs all three FinOps demos in sequence |
| `cost_estimate.py` | `run_cost_estimates()` — dry-run bytes + $ estimation for three query variants |
| `finops_queries.py` | `run_finops_audit()` — INFORMATION_SCHEMA top-queries + per-user spend |
| `scheduled_query.py` | `create_scheduled_query()` — DTS scheduled query creation |
| `deploy.sh` | Enables APIs, creates dataset, prints IAM note |
| `requirements.txt` | Python dependencies |

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
