# 45. Build a Custom MCP Server

> Build and run your own MCP server with FastMCP, exposing BigQuery and GCS as callable tools over Streamable HTTP — the same transport Google's managed MCP servers use. **GCP Services:** BigQuery, Cloud Storage, Cloud Run

## Architecture

```
┌─────────────────────────────────────┐
│  Custom MCP Server (FastMCP)        │
│  main.py → server.py                │
│                                     │
│  POST http://localhost:8080/mcp     │
│  (or https://your-service.run.app/mcp after deploy)
│                                     │
│  Tools:                             │
│  ┌───────────────────────────────┐  │
│  │ bq_query(sql: str) -> str     │──┼──► BigQuery.query()
│  │   max 20 rows, errors as text │  │    (your-project-id)
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │ gcs_list(prefix: str) -> str  │──┼──► storage.list_buckets()
│  │   buckets or objects by prefix│  │    storage.list_blobs()
│  └───────────────────────────────┘  │
└──────────────────┬──────────────────┘
                   │  Streamable HTTP (MCP protocol)
                   ▼
         client_example.py
         (or any MCP client: Gemini SDK, ADK MCPToolset, Claude, etc.)
```

## What this demonstrates

- FastMCP server definition: `mcp = FastMCP(...)` + `@mcp.tool()` decorators — no boilerplate MCP plumbing
- Streamable HTTP transport (`mcp.run(transport="streamable-http")`) — the same protocol Google's managed MCP servers expose on `googleapis.com/mcp`
- `stateless_http=True`: keeps each request self-contained, enabling load-balanced Cloud Run scale-to-zero
- Cloud Run deployment via `gcloud run deploy --source .` (buildpack, no Dockerfile required)
- Tool error handling: exceptions returned as text output so the MCP client (and any connected LLM) can reason about failures
- Client pattern: `streamablehttp_client` + `ClientSession` — identical to connecting to Google's managed servers

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

The script enables required APIs and prints both local-run and Cloud Run deployment commands.

## Run

**Local server (terminal 1):**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

**Example client (terminal 2):**

```bash
python client_example.py
```

**Deploy to Cloud Run:**

```bash
gcloud run deploy gcp-hub-mcp-server \
  --source . \
  --region=us-central1 \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=your-project-id \
  --allow-unauthenticated \
  --port=8080

# Test the deployed server:
SERVER_URL=https://your-service-url.run.app/mcp python client_example.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — starts the MCP server (Cloud Run container entrypoint) |
| `server.py` | FastMCP server with `bq_query` and `gcs_list` tool definitions |
| `client_example.py` | Example client: connects, lists tools, calls bq_query + gcs_list |
| `deploy.sh` | Enables APIs, prints local + Cloud Run deployment instructions |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
