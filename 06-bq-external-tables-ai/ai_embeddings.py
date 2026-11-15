# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
BigQuery AI Embeddings via ML.GENERATE_EMBEDDING.

Generates text embeddings in SQL via a remote model backed by a BigQuery
Cloud Resource connection to Vertex AI.

REQUIRES EXTRA SETUP — run deploy.sh which prints exact commands, or see:
  1) Create a BQ Cloud Resource connection in the US region.
  2) Grant the connection's service account roles/aiplatform.user.
  3) Re-run this script.

The remote model is just a pointer to a Vertex AI endpoint; the connection's
service account is what actually calls Vertex AI (no API key needed in code).
"""

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")
CONNECTION_ID = os.environ.get("BQ_CONNECTION_ID", "vertex-ai-conn")

MODEL_ID = f"{PROJECT_ID}.{DATASET_ID}.text_embedding_model"

SETUP_HELP = f"""
SETUP REQUIRED — the remote model/connection is not ready.

  1) Create the BQ connection (region must match dataset location — US):
       bq mk --connection --location=US --project_id={PROJECT_ID} \\
          --connection_type=CLOUD_RESOURCE {CONNECTION_ID}

  2) Find the connection's service account:
       bq show --connection {PROJECT_ID}.US.{CONNECTION_ID}

  3) Grant it Vertex AI access:
       gcloud projects add-iam-policy-binding {PROJECT_ID} \\
          --member="serviceAccount:<connection-sa>" \\
          --role="roles/aiplatform.user"

Then re-run: python main.py
"""


def create_remote_model(client: bigquery.Client) -> bool:
    """Create or replace the remote embedding model. Returns True on success."""
    try:
        client.query(f"""
            CREATE OR REPLACE MODEL `{MODEL_ID}`
            REMOTE WITH CONNECTION `{PROJECT_ID}.US.{CONNECTION_ID}`
            OPTIONS (ENDPOINT = 'text-embedding-005')
        """).result()
        print(f"Remote model ready: {MODEL_ID}")
        return True
    except Exception as exc:
        print(f"Could not create remote model: {exc}")
        print(SETUP_HELP)
        return False


def generate_embeddings(client: bigquery.Client, texts: list[str]) -> None:
    """Run ML.GENERATE_EMBEDDING over the provided texts and print results."""
    # Build a UNION ALL literal from the text list
    union_sql = "\n                 UNION ALL\n                 ".join(
        f"SELECT {repr(t)} AS content" for t in texts
    )

    try:
        rows = client.query(f"""
            SELECT
                content,
                ARRAY_LENGTH(ml_generate_embedding_result) AS dims,
                ml_generate_embedding_result[OFFSET(0)]    AS first_value
            FROM ML.GENERATE_EMBEDDING(
                MODEL `{MODEL_ID}`,
                ({union_sql})
            )
        """).result()
    except Exception as exc:
        print(f"Embedding query failed: {exc}")
        print(SETUP_HELP)
        return

    print("\n=== Embeddings ===")
    for row in rows:
        print(f"  text        : {row.content}")
        print(f"  dims        : {row.dims}, first value: {row.first_value:.6f}\n")

    print("Tip: pair with VECTOR_SEARCH() for in-warehouse semantic search.")
    print("     Same connection also powers AI.GENERATE / ML.GENERATE_TEXT")
    print("     with a Gemini endpoint (e.g. ENDPOINT = 'gemini-2.0-flash').")
