# 46. Deploy MCP Server to Cloud Run

> Package a FastMCP server as a private Cloud Run service with ID-token auth, then connect via a Python MCP client and MCP Toolbox for BigQuery. **GCP Services:** Cloud Run, BigQuery, Cloud Build, Artifact Registry

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Developer Machine                                          │
│                                                             │
│  cloud_run_deployer.py                                      │
│    └── generates: mcp_server_deploy/                        │
│         ├── server.py  (FastMCP + bq_query tool)            │
│         ├── requirements.txt                                │
│         └── Dockerfile                                      │
│                                                             │
│  gcloud run deploy --source ./mcp_server_deploy             │
│    └── Cloud Build: builds image → Artifact Registry        │
│                        │                                    │
└────────────────────────┼────────────────────────────────────┘
                         ▼
              ┌──────────────────────┐
              │  Cloud Run Service   │
              │  (--no-allow-unauth) │
              │  FastMCP server      │
              │  streamable-http     │
              └──────────┬───────────┘
                         │  ID Token (Bearer)
          ┌──────────────┴──────────────┐
          │                             │
   mcp_client.py                 toolbox_client.py
   (direct MCP SDK)              (MCP Toolbox SDK)
          │                             │
          ▼                             ▼
    bq_query tool              tools.yaml → Toolbox binary
    gcs_list tool              execute_sql / search_datasets
          │                             │
          └──────────────┬──────────────┘
                         ▼
                    BigQuery API
```

## What this demonstrates

- Packaging a FastMCP server into a Docker image via Cloud Build source deploy
- Private Cloud Run MCP service using `--no-allow-unauthenticated` + ID token auth
- Python MCP client connecting over Streamable HTTP with `mcp.ClientSession`
- MCP Toolbox for Databases: YAML-driven BigQuery tools, no server code required
- Full security model: `roles/run.invoker` grants + Google-signed ID tokens

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1

gcloud auth login
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

The script enables APIs and prints the exact `gcloud run deploy` command. Deploy is intentionally manual so you review the command before incurring Cloud Build + Cloud Run costs.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Generate artifacts + print all instructions
python main.py

# (After deploying) run MCP client against local server
# Terminal 1: cd mcp_server_deploy && python server.py
# Terminal 2:
python mcp_client.py

# MCP Toolbox BigQuery demo (generates tools.yaml, live call if toolbox running)
python toolbox_client.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrates all three steps: deployer → client instructions → toolbox demo |
| `cloud_run_deployer.py` | Generates `mcp_server_deploy/` (server.py + Dockerfile + requirements.txt) and prints deploy commands |
| `mcp_client.py` | MCP client connecting to local or Cloud Run FastMCP server via Streamable HTTP |
| `toolbox_client.py` | MCP Toolbox BigQuery: generates tools.yaml, prints run instructions, attempts live SDK call |
| `deploy.sh` | Enables APIs, generates artifacts, prints deploy + IAM commands |
| `requirements.txt` | Python dependencies |

## Cost notes

- `gcloud run deploy --source` triggers a Cloud Build job (small cost for image build)
- Cloud Run bills per request (generous free tier; idle cost near zero with scale-to-zero)
- BigQuery query cost: `SELECT 1` costs nothing; real queries billed per TB scanned
- MCP Toolbox binary is free/open-source; runs locally or on any compute

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
