# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Store and query embeddings in BigQuery using ML.GENERATE_EMBEDDING.
Source: 08_embeddings_in_bigquery.py (not_tested)

This pattern keeps embeddings inside the warehouse — no data leaves BigQuery.
Requires a one-time BigQuery Cloud Resource connection setup (see SETUP_HELP).

NOTE: This module requires a pre-configured BigQuery Cloud Resource connection
with an IAM grant to Vertex AI. Run with --help-setup to see setup instructions.
"""

import os

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

DATASET = "your_dataset"         # BigQuery dataset in US multi-region
CONNECTION_ID = "embedding_conn"  # BigQuery Cloud Resource connection name
MODEL_NAME = f"{PROJECT_ID}.{DATASET}.embedding_model"

SETUP_HELP = f"""
BigQuery remote embedding model not found. One-time setup required:

  # 1. Create a Cloud Resource connection in the US multi-region:
  bq mk --connection --location=US --project_id={PROJECT_ID} \\
      --connection_type=CLOUD_RESOURCE {CONNECTION_ID}

  # 2. Find the service account for the connection:
  bq show --connection {PROJECT_ID}.US.{CONNECTION_ID}

  # 3. Grant the connection's SA access to Vertex AI:
  gcloud projects add-iam-policy-binding {PROJECT_ID} \\
      --member="serviceAccount:<connection-sa-from-step-2>" \\
      --role="roles/aiplatform.user"

  # 4. Re-run this script — it will create the remote model automatically.

Why BigQuery embeddings?
  - No data leaves the warehouse (privacy, compliance)
  - Embed millions of rows in-place with a single SQL statement
  - Follow up with VECTOR_SEARCH() for nearest-neighbor retrieval in SQL
"""

CREATE_MODEL_SQL = f"""
CREATE MODEL IF NOT EXISTS `{MODEL_NAME}`
REMOTE WITH CONNECTION `{PROJECT_ID}.US.{CONNECTION_ID}`
OPTIONS (ENDPOINT = 'gemini-embedding-001')
"""

EMBED_SQL = f"""
SELECT
  content,
  ARRAY_LENGTH(ml_generate_embedding_result) AS dims,
  ml_generate_embedding_result[OFFSET(0)]    AS first_value
FROM ML.GENERATE_EMBEDDING(
  MODEL `{MODEL_NAME}`,
  (
    SELECT 'BigQuery embeds text without data ever leaving the warehouse.' AS content
    UNION ALL
    SELECT 'ML.GENERATE_EMBEDDING runs batch embedding jobs in pure SQL.'
    UNION ALL
    SELECT 'VECTOR_SEARCH pairs with ML.GENERATE_EMBEDDING for nearest-neighbor retrieval.'
  ),
  STRUCT(TRUE AS flatten_json_output, 'RETRIEVAL_DOCUMENT' AS task_type)
)
"""

VECTOR_SEARCH_EXAMPLE_SQL = f"""
-- Example: nearest-neighbor search using VECTOR_SEARCH (SQL only, no Python loop)
-- Requires a table with an embedding column first — see ML.GENERATE_EMBEDDING above.
--
-- SELECT base.content, distance
-- FROM VECTOR_SEARCH(
--   TABLE `{PROJECT_ID}.{DATASET}.your_embedded_table`,
--   'embedding_column',
--   (SELECT ml_generate_embedding_result AS embedding
--    FROM ML.GENERATE_EMBEDDING(
--      MODEL `{MODEL_NAME}`,
--      (SELECT 'your search query here' AS content),
--      STRUCT('RETRIEVAL_QUERY' AS task_type)
--    )),
--   top_k => 5
-- )
-- ORDER BY distance;
"""


def get_bq_client() -> bigquery.Client:
    """Return a BigQuery client for the configured project."""
    return bigquery.Client(project=PROJECT_ID)


def ensure_dataset(client: bigquery.Client) -> None:
    """Create the dataset if it does not exist (idempotent)."""
    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET}")
    client.create_dataset(dataset_ref, exists_ok=True)
    print(f"Dataset ready : {PROJECT_ID}.{DATASET}")


def create_remote_model(client: bigquery.Client) -> bool:
    """
    Create the BigQuery remote model backed by gemini-embedding-001.
    Returns True if successful, False if the connection or IAM is missing.
    """
    try:
        client.query(CREATE_MODEL_SQL).result()
        client.get_model(MODEL_NAME)
        print(f"Remote model ready: {MODEL_NAME}")
        return True
    except (NotFound, Exception) as exc:
        if "connection" in str(exc).lower() or isinstance(exc, NotFound):
            print(SETUP_HELP)
            return False
        raise


def run_embedding_query(client: bigquery.Client) -> None:
    """
    Run ML.GENERATE_EMBEDDING over sample rows and print the results.
    Shows dims, first vector value, and source content for each row.
    """
    print("Running ML.GENERATE_EMBEDDING over 3 sample rows...")
    print("-" * 70)

    for row in client.query(EMBED_SQL).result():
        print(f"  dims={row.dims}  first_value={row.first_value:+.6f}")
        print(f"  content: {row.content}")
        print()

    print("-" * 70)
    print("Use VECTOR_SEARCH() after ML.GENERATE_EMBEDDING for full SQL-native RAG.")
    print()
    print("VECTOR_SEARCH example SQL:")
    print(VECTOR_SEARCH_EXAMPLE_SQL)


def run_bq_vectors_demo() -> None:
    """
    Full BigQuery vector embedding demo:
      1. Ensure dataset exists
      2. Create remote model (prints setup help if connection missing)
      3. Run ML.GENERATE_EMBEDDING and show results
    """
    client = get_bq_client()

    ensure_dataset(client)

    if not create_remote_model(client):
        return  # setup help already printed

    run_embedding_query(client)
