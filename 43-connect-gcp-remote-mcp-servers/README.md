# 43. Connect to GCP Remote MCP Servers

> Connect to Google Cloud-managed remote MCP servers (Developer Knowledge + BigQuery) using Streamable HTTP and ADC. **GCP Services:** Developer Knowledge MCP, BigQuery MCP, Cloud IAM

## Architecture

```
Local Python Client
        │
        │  ADC (gcloud auth application-default login)
        │  OAuth2 Bearer token + x-goog-user-project header
        │
        ├──► https://developerknowledge.googleapis.com/mcp
        │           │
        │           ├── initialize() handshake
        │           ├── list_tools()  →  search_documents, fetch_docs, ...
        │           └── call_tool()   →  GCP docs answer
        │
        └──► https://bigquery.googleapis.com/mcp
                    │
                    ├── initialize() handshake
                    ├── list_tools()  →  list_datasets, run_query, ...
                    └── call_tool()   →  datasets / query result
```

## What this demonstrates

- Universal pattern for connecting to any `https://<service>.googleapis.com/mcp` endpoint
- Streamable HTTP transport (`mcp.client.streamable_http`) — same transport Google's own MCP servers use
- ADC-based OAuth2 auth: works locally (gcloud ADC) and on GCP (attached service account)
- Critical two-command setup: `gcloud services enable` + `gcloud beta services mcp enable` — 200 on initialize does NOT prove the MCP surface is active
- Tool discovery (`session.list_tools()`) and invocation (`session.call_tool()`) in a live `ClientSession`
- Developer Knowledge MCP for GCP docs grounding; BigQuery MCP for dataset listing and SQL

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

The script enables both APIs AND their MCP surfaces. Skipping the `gcloud beta services mcp enable` step is the most common cause of 403 errors after a successful `initialize()`.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator — connects to both MCP servers sequentially |
| `developer_knowledge_mcp.py` | Developer Knowledge MCP client: connect, list tools, query |
| `bigquery_mcp.py` | BigQuery Managed MCP client: connect, list datasets, run SQL |
| `deploy.sh` | Enables APIs + MCP surfaces, creates BQ dataset, configures auth |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
