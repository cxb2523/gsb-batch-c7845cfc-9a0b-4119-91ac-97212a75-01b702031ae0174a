# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Custom MCP Server with FastMCP — BigQuery + GCS Tools.

Exposes two real GCP tools over Streamable HTTP on port 8080:
  - bq_query: run SQL on BigQuery, return rows as text (max 20 rows)
  - gcs_list: list GCS buckets or objects under a prefix

This exact pattern containerizes cleanly for Cloud Run:
  - Stateless + JSON responses => compatible with scale-to-zero
  - Cloud Run injects PORT env var automatically
  - Transport (streamable-http) matches Google's own managed MCP servers

Run locally:
  python server.py
  # Then connect a client to http://localhost:8080/mcp

Deploy to Cloud Run:
  See deploy.sh for the full containerized deployment steps.
"""

import os

from mcp.server.fastmcp import FastMCP

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
PORT = int(os.environ.get("PORT", "8080"))

# FastMCP handles the MCP protocol plumbing; you just declare tools.
# streamable-http transport = single /mcp endpoint, the same transport
# Google's own managed remote MCP servers use. Stateless + JSON responses
# keep the server friendly to load-balanced, scale-to-zero Cloud Run.
mcp = FastMCP(
    "gcp-data-hub-tools",
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,
)


@mcp.tool()
def bq_query(sql: str) -> str:
    """Run a BigQuery SQL query and return rows as text (max 20 rows).

    Args:
        sql: A standard SQL query, e.g. "SELECT 1 AS ok".
    """
    from google.cloud import bigquery

    client = bigquery.Client(project=PROJECT_ID)
    try:
        rows = list(client.query(sql).result(max_results=20))
    except Exception as exc:  # noqa: BLE001 - return errors as tool output
        return f"BigQuery error: {exc}"
    if not rows:
        return "Query returned no rows."
    lines = [", ".join(rows[0].keys())]
    lines += [", ".join(str(v) for v in row.values()) for row in rows]
    return "\n".join(lines)


@mcp.tool()
def gcs_list(prefix: str = "") -> str:
    """List GCS buckets in the project, or objects when prefix is 'bucket/path'.

    Args:
        prefix: Empty string to list all buckets; "bucket-name/optional/path"
                to list objects inside that bucket under the given path.
    """
    from google.cloud import storage

    client = storage.Client(project=PROJECT_ID)
    try:
        if not prefix:
            buckets = [b.name for b in client.list_buckets(max_results=20)]
            return "Buckets:\n" + "\n".join(buckets) if buckets else "No buckets found."
        bucket_name, _, object_prefix = prefix.partition("/")
        blobs = client.list_blobs(bucket_name, prefix=object_prefix, max_results=20)
        names = [b.name for b in blobs]
        return (
            f"Objects in {bucket_name}/{object_prefix}:\n" + "\n".join(names)
            if names
            else "No objects found."
        )
    except Exception as exc:  # noqa: BLE001
        return f"GCS error: {exc}"


def start_server():
    """Start the FastMCP server over Streamable HTTP."""
    print("=== GCP Data Hub Custom MCP Server (FastMCP) ===")
    print(f"Project   : {PROJECT_ID}")
    print(f"Endpoint  : http://localhost:{PORT}/mcp")
    print("Tools     : bq_query(sql), gcs_list(prefix)")
    print("Transport : streamable-http (same as Google's managed MCP servers)")
    print("Ctrl+C to stop.\n")
    mcp.run(transport="streamable-http")
