"""
BigQuery ML functions: train, evaluate, and predict with a linear regression model.

Trains a linear_reg model in pure SQL to predict total_amount from quantity,
unit_price, and category on the sales fixture. No Python ML stack needed —
BQML runs training and inference inside BigQuery using standard SQL.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")
BUCKET_NAME = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

SALES_TABLE = f"{PROJECT_ID}.{DATASET_ID}.sales_data"
MODEL_ID = f"{PROJECT_ID}.{DATASET_ID}.sales_amount_model"
GCS_URI = f"gs://{BUCKET_NAME}/data/sales_data.csv"


def ensure_fixture(client: bigquery.Client) -> None:
    """
    Create the sales fixture table from GCS if it does not already exist.

    Checks for the table first — avoids overwriting data on repeated runs.
    Uses schema autodetect for simplicity; swap for explicit schema in production.

    Args:
        client: BigQuery client.
    """
    try:
        client.get_table(SALES_TABLE)
        print(f"Fixture table exists : {SALES_TABLE}")
    except Exception:
        cfg = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            autodetect=True,
        )
        client.load_table_from_uri(GCS_URI, SALES_TABLE, job_config=cfg).result()
        print(f"Fixture table loaded : {SALES_TABLE}")


def train_model(client: bigquery.Client) -> None:
    """
    Train a BigQuery ML linear regression model using CREATE OR REPLACE MODEL.

    BQML one-hot-encodes STRING features (category) automatically.
    The model learns to predict total_amount from quantity, unit_price,
    and category. Training runs entirely in BigQuery — no Python ML stack,
    no data export.

    Args:
        client: BigQuery client.
    """
    print("\nTraining linear_reg model (CREATE OR REPLACE MODEL) ...")
    client.query(f"""
        CREATE OR REPLACE MODEL `{MODEL_ID}`
        OPTIONS (
            model_type = 'linear_reg',
            input_label_cols = ['total_amount']
        ) AS
        SELECT quantity, unit_price, category, total_amount
        FROM `{SALES_TABLE}`
        WHERE total_amount IS NOT NULL
    """).result()
    print(f"Model ready: {MODEL_ID}")


def evaluate_model(client: bigquery.Client) -> None:
    """
    Evaluate the trained model using ML.EVALUATE.

    Prints MAE, MSE, and R^2. For the sales fixture total_amount =
    quantity * unit_price so R^2 should be very high (near 1.0).

    Args:
        client: BigQuery client.
    """
    print("\n=== ML.EVALUATE ===")
    for row in client.query(
        f"SELECT * FROM ML.EVALUATE(MODEL `{MODEL_ID}`)"
    ).result():
        print(f"  MAE  : {row.mean_absolute_error:,.2f}")
        print(f"  MSE  : {row.mean_squared_error:,.2f}")
        print(f"  R^2  : {row.r2_score:.4f}")


def predict(client: bigquery.Client, limit: int = 5) -> None:
    """
    Run ML.PREDICT on a sample of rows and print actual vs predicted values.

    ML.PREDICT adds a predicted_<label> column to the input rows. The input
    query selects the same feature columns used during training.

    Args:
        client: BigQuery client.
        limit:  Number of rows to predict on (default: 5).
    """
    print(f"\n=== ML.PREDICT (sample of {limit}) ===")
    rows = client.query(f"""
        SELECT category, quantity, unit_price,
               total_amount        AS actual,
               predicted_total_amount AS predicted
        FROM ML.PREDICT(
            MODEL `{MODEL_ID}`,
            (SELECT quantity, unit_price, category, total_amount
             FROM `{SALES_TABLE}` LIMIT {limit})
        )
    """).result()
    print(f"{'CATEGORY':<15} {'QTY':>4} {'PRICE':>9} {'ACTUAL':>10} {'PREDICTED':>10}")
    for r in rows:
        print(
            f"{r.category:<15} {r.quantity:>4} {r.unit_price:>9.2f} "
            f"{r.actual:>10.2f} {r.predicted:>10.2f}"
        )

    print("\nNote: total_amount = quantity * unit_price, so R^2 should be very high.")


def run_train_evaluate_predict(project_id: str = PROJECT_ID) -> None:
    """
    Full BQML pipeline: ensure fixture → train → evaluate → predict.

    Args:
        project_id: GCP project to run BQML in.
    """
    client = bigquery.Client(project=project_id)
    ensure_fixture(client)
    train_model(client)
    evaluate_model(client)
    predict(client)
