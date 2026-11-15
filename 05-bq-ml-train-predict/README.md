# 5. BigQuery ML: Train, Evaluate, Predict

> End-to-end ML pipeline in pure SQL — no Python ML stack. BigQuery ML trains a linear regression model, evaluates it with ML.EVALUATE, and scores new rows with ML.PREDICT. **GCP Services:** BigQuery (BQML), Cloud Storage

## Architecture

```
GCS CSV ──► BigQuery Table (sales_data)
                    │
                    ▼
        CREATE OR REPLACE MODEL (linear_reg)
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
    ML.EVALUATE          ML.PREDICT
  (MAE / MSE / R^2)   (actual vs predicted)
```

## What this demonstrates

- `CREATE OR REPLACE MODEL` with `model_type = 'linear_reg'` and `input_label_cols` — entire training in SQL
- BQML automatic one-hot encoding of STRING features (category column)
- `ML.EVALUATE` returning MAE, MSE, and R^2 metrics without leaving BigQuery
- `ML.PREDICT` adding a `predicted_<label>` column to input rows — batch scoring in SQL
- The "no export" pattern: data never leaves BigQuery for training or inference
- Idempotent fixture loading: `get_table()` check before loading from GCS

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id   # ← replace
export GOOGLE_CLOUD_LOCATION=us-central1
export GCS_BUCKET=your-gcs-bucket             # ← replace
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
| `main.py` | Entry point — orchestrates fixture → train → evaluate → predict |
| `model.py` | `ensure_fixture()`, `train_model()`, `evaluate_model()`, `predict()` |
| `data/sales_data.csv` | Sample sales fixture (20 rows, GCP product orders) |
| `deploy.sh` | Enables APIs, creates dataset, bucket, uploads CSV |
| `requirements.txt` | Python dependencies |

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
