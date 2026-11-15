"""
BigQuery ML: Train, Evaluate, Predict — entry point.

Orchestrates the full BQML pipeline in three stages:
  1. Train  — CREATE OR REPLACE MODEL with linear_reg in pure SQL
  2. Evaluate — ML.EVALUATE for MAE, MSE, R^2 metrics
  3. Predict — ML.PREDICT on a 5-row sample, actual vs predicted

No Python ML stack needed — training and inference run entirely inside
BigQuery using standard SQL. Requires the sales fixture on GCS.

Usage:
    python main.py
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from google.cloud import bigquery

from model import ensure_fixture, train_model, evaluate_model, predict

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

SEPARATOR = "=" * 60


def section(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def main():
    client = bigquery.Client(project=PROJECT_ID)

    section("Step 1: Ensure Sales Fixture Table")
    ensure_fixture(client)

    section("Step 2: Train Linear Regression Model (BQML)")
    train_model(client)

    section("Step 3: Evaluate Model (ML.EVALUATE)")
    evaluate_model(client)

    section("Step 4: Predict on Sample Rows (ML.PREDICT)")
    predict(client, limit=5)

    print(f"\n{SEPARATOR}")
    print("BQML pipeline complete: train → evaluate → predict.")
    print("No Python ML stack used — everything ran inside BigQuery SQL.")


if __name__ == "__main__":
    main()
