# 34. Data Quality Validation Framework

> Pre-load data quality gate in plain pandas: schema, null, range, uniqueness, and consistency checks before any BigQuery load. **GCP Services:** (local pandas gate — integrates with Cloud Run Jobs, Cloud Composer)

## Architecture

```
data/sales_data.csv
        │
        ▼
    main.py: load_dataset()
        │
        ▼
  validators.py: run_all_checks()
     ├─ check_column_set()          ← schema
     ├─ check_dtypes()              ← schema
     ├─ check_nulls()               ← completeness
     ├─ check_quantity_positive()   ← ranges
     ├─ check_unit_price_positive() ← ranges
     ├─ check_order_date_not_future()← ranges
     ├─ check_order_id_unique()     ← uniqueness
     ├─ check_region_valid()        ← referential
     └─ check_total_amount_derived()← consistency
        │
        ▼
  reporter.py: print_report()
        │
        ├─ Console: grouped PASS/FAIL report
        └─ (opt) JSON: data/quality_report.json
        │
        ▼
  Exit 0 (PASS) → BQ load step proceeds
  Exit 1 (FAIL) → orchestrator stops pipeline
```

## What this demonstrates

- Pre-load data quality gate: fail at the landing zone, not in the warehouse
- Schema validation with pandas dtype category checks (robust across pandas 2.x/3.x upgrades)
- Null checks on required key columns (order_id, order_date, total_amount)
- Range checks: positive quantity, positive unit_price, no future-dated orders
- Uniqueness: business key (`order_id`) deduplication check
- Referential integrity: region allow-list validation
- Cross-column consistency: `total_amount == quantity * unit_price` (±0.01 tolerance)
- Structured reporter with grouping, summary counts, and optional JSON export
- Exit code 1 on failure — wires directly into Cloud Run Jobs, Composer DAGs, CI pipelines

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
```

No GCP authentication required — runs entirely locally with pandas.

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

With JSON report output:

```bash
SAVE_REPORT=true python main.py
```

The JSON report is written to `data/quality_report.json` and includes verdict, timestamps, per-check results, and failure details.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Loads CSV, runs all checks, prints report, exits non-zero on failure |
| `validators.py` | All validation check functions: schema, null, range, uniqueness, consistency |
| `reporter.py` | Console report formatter and JSON report builder/writer |
| `data/sales_data.csv` | Sample 20-row GCP sales dataset used as validation target |
| `deploy.sh` | Setup instructions and GCP pipeline integration guidance |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
