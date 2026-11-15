# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Cloud Run MCP Server Deployer

Generates a ready-to-deploy folder ./mcp_server_deploy/ containing:
  - server.py    : FastMCP server with BigQuery tool, streamable-http on $PORT
  - requirements.txt
  - Dockerfile

Also prints the exact gcloud run deploy command and authenticated client snippet.
This module generates artifacts ONLY — it does not deploy anything.
"""

import os
from pathlib import Path


SERVICE = "techtrapture-mcp-server"


SERVER_PY = '''\
"""Minimal MCP server for Cloud Run — streamable-http on $PORT."""
import os
from mcp.server.fastmcp import FastMCP

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

mcp = FastMCP(
    "techtrapture-mcp",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    stateless_http=True,
)


@mcp.tool()
def bq_query(sql: str) -> str:
    """Run a BigQuery SQL query and return up to 20 rows as text."""
    from google.cloud import bigquery
    client = bigquery.Client(project=PROJECT_ID)
    try:
        rows = list(client.query(sql).result(max_results=20))
    except Exception as exc:
        return f"BigQuery error: {exc}"
    if not rows:
        return "Query returned no rows."
    lines = [", ".join(rows[0].keys())]
    lines += [", ".join(str(v) for v in r.values()) for r in rows]
    return "\\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
'''

REQUIREMENTS = "mcp\ngoogle-cloud-bigquery\n"

DOCKERFILE = """\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
# Cloud Run injects PORT; FastMCP reads it in server.py
CMD ["python", "server.py"]
"""


def _build_client_snippet(service: str, region: str) -> str:
    return f'''\
import asyncio
import google.auth.transport.requests
import google.oauth2.id_token
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVICE_URL = "https://{service}-<hash>-uc.a.run.app"  # from deploy output

async def main():
    # ID token AUDIENCE must be the Cloud Run service URL (not an OAuth access token).
    auth_req = google.auth.transport.requests.Request()
    id_token = google.oauth2.id_token.fetch_id_token(auth_req, SERVICE_URL)
    headers = {{"Authorization": f"Bearer {{id_token}}"}}
    async with streamablehttp_client(f"{{SERVICE_URL}}/mcp", headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool("bq_query", {{"sql": "SELECT 1 AS ok"}})
            print(result.content[0].text)

asyncio.run(main())
'''


def generate_deployment_artifacts(
    project_id: str = "your-project-id",
    region: str = "us-central1",
    out_dir_path: str = "./mcp_server_deploy",
) -> Path:
    """Write server.py, requirements.txt, and Dockerfile to out_dir_path.

    Returns the output directory as a Path object.
    """
    out_dir = Path(out_dir_path)
    out_dir.mkdir(exist_ok=True)

    files = {
        "server.py": SERVER_PY,
        "requirements.txt": REQUIREMENTS,
        "Dockerfile": DOCKERFILE,
    }

    print(f"Writing deployment folder: {out_dir.resolve()}")
    for name, content in files.items():
        (out_dir / name).write_text(content)
        print(f"  [written] {name}")

    print("\n--- Deploy command (source-based build, private service) ---")
    print(f"""\
gcloud run deploy {SERVICE} \\
  --source {out_dir_path} \\
  --project {project_id} \\
  --region {region} \\
  --no-allow-unauthenticated \\
  --set-env-vars GOOGLE_CLOUD_PROJECT={project_id}""")

    print(
        "\n--no-allow-unauthenticated keeps the service private. Callers need\n"
        "roles/run.invoker and must send a Google-signed ID token."
    )

    print("\n--- Quick curl test from terminal ---")
    print("  TOKEN=$(gcloud auth print-identity-token)")
    print("  curl -H \"Authorization: Bearer $TOKEN\" https://<service-url>/mcp")

    print("\n--- Python client snippet (ID token via ADC) ---")
    print(_build_client_snippet(SERVICE, region))

    print("--- Grant caller invoke rights ---")
    print(f"  gcloud run services add-iam-policy-binding {SERVICE} \\")
    print(f"    --region {region} --member=user:you@example.com --role=roles/run.invoker")

    return out_dir


if __name__ == "__main__":
    generate_deployment_artifacts(
        project_id=os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id"),
        region=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )
