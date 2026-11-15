# 44. Gemini & ADK with MCP Tools

> Two patterns for integrating remote MCP tools with Gemini: direct SDK automatic function calling and ADK MCPToolset agent. **GCP Services:** Vertex AI, Developer Knowledge MCP, Google ADK

## Architecture

```
                    https://developerknowledge.googleapis.com/mcp
                                        │
                         ADC OAuth2 Bearer token
                                        │
         ┌──────────────────────────────┼──────────────────────────┐
         │                             │                           │
         ▼                             │                           ▼
┌─────────────────────┐                │             ┌──────────────────────┐
│ PATTERN 1:          │                │             │ PATTERN 2:           │
│ Gemini SDK Direct   │                │             │ ADK Agent            │
│                     │                │             │                      │
│ ClientSession       │◄───────────────┘             │ MCPToolset           │
│ passed as tool to   │                              │  └─ StreamableHTTP   │
│ generate_content()  │                              │     ConnectionParams │
│                     │                              │                      │
│ Automatic function  │                              │ Agent(               │
│ calling loop        │                              │   model=gemini-2.5,  │
│ managed by SDK      │                              │   tools=[toolset])   │
│                     │                              │                      │
│ → Final answer      │                              │ InMemoryRunner.      │
│   with citations    │                              │   run_async()        │
└─────────────────────┘                              └──────────────────────┘
```

## What this demonstrates

- **Direct SDK pattern**: pass a live MCP `ClientSession` as a tool to `generate_content()` — the google-genai SDK handles MCP schema translation and the full automatic function calling loop
- **ADK agent pattern**: `MCPToolset` + `StreamableHTTPConnectionParams` wraps any remote MCP server as native ADK tools — agent handles multi-turn reasoning and tool orchestration
- Automatic function calling history: `response.automatic_function_calling_history` exposes which MCP tools were invoked and in what order
- ADK `InMemoryRunner` for lightweight single-turn testing; same agent runs in `adk web` for interactive UI
- Both patterns use identical ADC authentication and MCP transport — swap the MCP URL to target any googleapis.com/mcp endpoint

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator — runs both patterns sequentially |
| `gemini_mcp.py` | Gemini SDK direct integration: ClientSession passed as tool |
| `adk_mcp_agent.py` | ADK Agent with MCPToolset and InMemoryRunner |
| `deploy.sh` | Enables APIs + MCP surface, configures auth |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
