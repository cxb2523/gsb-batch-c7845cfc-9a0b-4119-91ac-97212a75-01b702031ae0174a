# 13. Gemini Function Calling & Tools

> Function calling (automatic + manual), Pydantic schema-constrained JSON extraction, and stateful multi-turn chat with system instructions. **GCP Services:** Vertex AI (Gemini API)

## Architecture

```
User Prompt
    │
    ▼
genai.Client → models.generate_content()
    │
    ├── tools=[callable]          ← automatic: SDK runs the full agentic loop
    ├── FunctionDeclaration       ← manual: YOU execute + return results
    ├── response_schema=Pydantic  ← constrained JSON output, validated on receipt
    └── client.chats.create()    ← stateful session, history replayed each turn
                                     (system_instruction sets persistent persona)
```

## What this demonstrates

- Automatic function calling: pass Python callables — SDK handles schema extraction, execution, and result injection in one call
- Manual function calling via `FunctionDeclaration`: explicit control over execution, useful for async tools, approval workflows, or audit trails
- Schema-constrained JSON via `response_schema` + `response_mime_type="application/json"` — extract structured fields from messy unstructured text
- `response.parsed` returns a validated Pydantic instance directly — no `json.loads`, no manual validation
- System instructions: same prompt, different behavior — persona, format, and guardrail isolation
- Multi-turn chat via `client.chats`: history accumulated client-side, replayed on every turn; cost grows with history length

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
gcloud auth application-default login
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
| `main.py` | Orchestrates all three patterns in sequence |
| `function_calling.py` | Automatic and manual function calling flows |
| `structured_output.py` | Pydantic schema-constrained JSON extraction |
| `chat.py` | System instructions comparison + multi-turn chat session |
| `deploy.sh` | Enable Vertex AI API and set project |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
