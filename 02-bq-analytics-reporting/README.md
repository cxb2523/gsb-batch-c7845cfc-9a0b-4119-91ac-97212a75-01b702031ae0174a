# 2. BigQuery Analytics & Reporting

> Query execution, parameterized SQL, pandas DataFrame export, and dataset metadata inspection — the four core BQ reporting patterns. **GCP Services:** BigQuery

## Architecture

```
BigQuery Public Dataset (shakespeare) ──► Simple Query      ──► Printed Results
                                      ──► Parameterized SQL ──► Named Params (injection-safe)
                                      ──► to_dataframe()    ──► pandas DataFrame ──► Last-mile Analysis
BQ Project Metadata API              ──► list_datasets()    ──► Storage / Inventory Report
                                      ──► get_table()       ──► Rows, Size, Schema, Timestamps
```

## What this demonstrates

- Running plain SQL and reading results via `RowIterator` with attribute and index access
- Named query parameters (`ScalarQueryParameter`) for SQL injection prevention and plan caching
- `to_dataframe()` for moving BQ results into pandas (requires `db-dtypes` for type mapping)
- `list_datasets()` + `list_tables()` + `get_table()` for project inventory and storage audits
- Reading `query_job.total_bytes_processed` and `cache_hit` for cost awareness post-query

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id   # ← replace
export GOOGLE_CLOUD_LOCATION=us-central1
export BQ_DATASET=your_dataset                # ← replace
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
| `main.py` | Entry point — runs all four analytics demos in sequence |
| `queries.py` | `run_simple_query()` and `run_parameterized_query()` |
| `reporting.py` | `query_to_dataframe()` and `list_datasets_tables_metadata()` |
| `data/sales_data.csv` | Sample sales fixture (reference data) |
| `deploy.sh` | Enables BigQuery API, creates dataset |
| `requirements.txt` | Python dependencies |

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
