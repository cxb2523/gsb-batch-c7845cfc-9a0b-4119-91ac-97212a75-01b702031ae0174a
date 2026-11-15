# 29. Data Format Processing

> Full data format tour using a single sales dataset: CSV loading (stdlib + pandas), JSON/JSONL conversion, pandas cleaning/transforms, and Parquet read/write with benchmarks. **No GCP credentials required** — all operations are local.

## Architecture

```
data/sales_data.csv
        │
        ├── Step 1: CSV Loading
        │          ├── stdlib csv.DictReader  (streaming, zero-deps)
        │          └── pandas.read_csv        (typed, vectorized)
        │
        ├── Step 2: JSON / JSONL
        │          ├── → output/sales_data.json   (one document)
        │          └── → output/sales_data.jsonl  (one record per line)
        │
        ├── Step 3: Pandas Clean + Transform
        │          ├── Deduplicate on order_id
        │          ├── Fill nulls per-column
        │          ├── Derive order_month / order_quarter
        │          ├── Category dtype (memory reduction)
        │          ├── Revenue by region aggregation
        │          └── Monthly revenue pivot by category
        │
        └── Step 4: Parquet Read/Write
                   ├── → output/sales_data.parquet (snappy)
                   ├── Full-table read benchmark vs CSV
                   └── Column-pruned read (2 of 9 cols)
```

## What this demonstrates

- `csv.DictReader` vs `pandas.read_csv` — when each is the right tool
- JSON vs JSONL: structural differences, BigQuery/Dataflow/Gemini batch use JSONL
- `pd.read_json(lines=True)` — the flag people forget for JSONL
- Standard cleaning pipeline: dedupe, null handling, derived columns, category dtype
- Parquet columnar format: schema survival, column pruning, size and read speed benchmarks
- `pyarrow` as the Parquet engine via pandas (`engine="pyarrow"`)

## Prerequisites

No GCP project or credentials required for this project. Only Python 3.8+ and pip.

```bash
# Optional: set project for consistency with other projects
export GOOGLE_CLOUD_PROJECT=your-project-id
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

Output files are written to `output/`.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — orchestrates all four format steps |
| `csv_handler.py` | CSV loading: stdlib DictReader and pandas.read_csv |
| `json_handler.py` | JSON/JSONL write, read, and streaming |
| `transformer.py` | Pandas cleaning pipeline: dedupe, nulls, derived columns, aggregations |
| `parquet_handler.py` | Parquet write/read with pyarrow, column pruning, benchmark |
| `data/sales_data.csv` | Sample sales dataset (20 rows) |
| `requirements.txt` | Python dependencies (pandas, pyarrow) |
| `deploy.sh` | Setup instructions (no GCP APIs needed) |

## Format Decision Guide

| Format | Use when |
|--------|----------|
| CSV | Raw extract from source systems; human-readable debugging |
| JSON | API payloads, configs, small documents |
| JSONL | BigQuery loads, Gemini batch prediction, Dataflow inputs, streaming append |
| Parquet | Anything analytical read more than once; lake storage standard |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
