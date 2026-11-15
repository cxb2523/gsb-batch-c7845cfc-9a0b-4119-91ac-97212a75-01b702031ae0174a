# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
BigQuery VECTOR_SEARCH RAG — load chunks with precomputed embeddings into a
BigQuery table and retrieve with the VECTOR_SEARCH SQL function.

BigQuery as a vector store suits teams already running analytics workloads
in BQ: no extra infrastructure, unified governance, and SQL-native querying
of both metadata filters and vector similarity in the same query.

Scaling tip: VECTOR_SEARCH without an index is brute-force O(n). Once your
table exceeds ~5K rows, create a VECTOR INDEX for ANN retrieval:
    CREATE VECTOR INDEX idx ON `your_table`(embedding)
    OPTIONS(distance_type='COSINE', index_type='IVF');
"""

import os

from google import genai
from google.genai import types
from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET = "your_dataset"
TABLE = f"{PROJECT_ID}.{DATASET}.rag_chunks"
EMBEDDING_MODEL = "gemini-embedding-001"
GENERATION_MODEL = "gemini-2.5-flash"
DIMS = 768   # truncated dimensionality — 4x cheaper than full 3072-dim
TOP_K = 3


def embed_chunks(gen_client: object, chunks: list[str]) -> list[dict]:
    """Embed all chunks and return rows ready for BigQuery insertion."""
    resp = gen_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=chunks,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT", output_dimensionality=DIMS
        ),
    )
    rows = [
        {"id": i, "content": chunk, "embedding": list(e.values)}
        for i, (chunk, e) in enumerate(zip(chunks, resp.embeddings))
    ]
    return rows


def load_to_bigquery(bq: object, rows: list[dict]) -> None:
    """Create (or replace) the BQ table and insert the embedded chunks."""
    bq.create_dataset(bigquery.Dataset(f"{PROJECT_ID}.{DATASET}"), exists_ok=True)
    bq.query(f"""
        CREATE OR REPLACE TABLE `{TABLE}` (
          id INT64,
          content STRING,
          embedding ARRAY<FLOAT64>
        )
    """).result()
    bq.insert_rows_json(TABLE, rows)
    print(f"[BQ] Loaded {len(rows)} chunks into {TABLE} ({DIMS}-dim vectors)")


def vector_search(bq: object, gen_client: object, question: str, top_k: int = TOP_K) -> list:
    """Embed the question and run VECTOR_SEARCH SQL to retrieve top-k chunks."""
    q_resp = gen_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=[question],
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY", output_dimensionality=DIMS
        ),
    )
    q_vec = list(q_resp.embeddings[0].values)

    search_sql = f"""
        SELECT base.content, distance
        FROM VECTOR_SEARCH(
          TABLE `{TABLE}`, 'embedding',
          (SELECT @query_embedding AS embedding),
          top_k => {top_k}, distance_type => 'COSINE'
        )
        ORDER BY distance
    """
    job = bq.query(
        search_sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("query_embedding", "FLOAT64", q_vec)
            ]
        ),
    )
    results = list(job.result())
    print(f"[BQ] Question: {question!r}")
    print(f"[BQ] VECTOR_SEARCH top {top_k}:")
    for row in results:
        print(f"  [distance={row.distance:.4f}] {row.content}")
    return results


def generate_answer(gen_client: object, results: list, question: str) -> str:
    """Generate a grounded answer from BQ VECTOR_SEARCH results."""
    context = "\n".join(row.content for row in results)
    prompt = f"Answer using ONLY this context:\n\n{context}\n\nQuestion: {question}"
    answer = gen_client.models.generate_content(model=GENERATION_MODEL, contents=prompt)
    print(f"[BQ] Answer:\n{answer.text}")
    return answer.text


def run(chunks: list[str], question: str) -> str:
    """
    End-to-end BQ VECTOR_SEARCH RAG: embed → load → search → generate.
    Returns the generated answer text.
    """
    gen_client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
    bq = bigquery.Client(project=PROJECT_ID)

    rows = embed_chunks(gen_client, chunks)
    load_to_bigquery(bq, rows)
    results = vector_search(bq, gen_client, question)
    return generate_answer(gen_client, results, question)
