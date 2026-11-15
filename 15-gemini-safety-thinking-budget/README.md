# 15. Gemini Safety Settings & Thinking Budget

> Per-category safety thresholds with production-safe blocked-response handling, plus thinking budget control for Gemini 2.5 — compare cost, latency, and answer quality across thinking OFF / dynamic / visible-thoughts modes. **GCP Services:** Vertex AI (Gemini API)

## Architecture

```
User Prompt
    │
    ▼
generate_content(safety_settings=[...])
    │
    ├── prompt_feedback.block_reason set? → PROMPT BLOCKED (no candidates)
    ├── candidate.finish_reason == SAFETY? → RESPONSE BLOCKED (inspect ratings)
    └── normal finish_reason → response.text safe to access

Reasoning Prompt
    │
    ▼
ThinkingConfig(thinking_budget=0)       → fastest / cheapest (thinking OFF)
ThinkingConfig(None)                    → dynamic budget (model self-calibrates)
ThinkingConfig(include_thoughts=True)  → visible thought summaries returned
    │
    ▼
usage_metadata.thoughts_token_count    → billed as output tokens
```

## What this demonstrates

- Four harm categories with configurable thresholds: `HARM_CATEGORY_HARASSMENT`, `HARM_CATEGORY_HATE_SPEECH`, `HARM_CATEGORY_DANGEROUS_CONTENT`, `HARM_CATEGORY_SEXUALLY_EXPLICIT`
- Threshold ladder: `BLOCK_LOW_AND_ABOVE` → `BLOCK_MEDIUM_AND_ABOVE` → `BLOCK_ONLY_HIGH` → `BLOCK_NONE`
- Production-safe response handling: check `prompt_feedback.block_reason` and `candidate.finish_reason` before accessing `response.text`
- Gemini 2.5 thinking tokens — billed as output, reported in `usage_metadata.thoughts_token_count`
- Three thinking modes compared: OFF (`budget=0`), dynamic default, and visible thoughts (`include_thoughts=True`)
- Cost / latency / quality trade-offs across thinking modes for multi-step reasoning prompts
- `include_thoughts=True` for explainability and debugging — inspect raw thought summaries

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
| `main.py` | Orchestrates safety and thinking budget demos in sequence |
| `safety.py` | Safety settings configuration and blocked-response handling |
| `thinking.py` | Thinking budget modes: OFF / dynamic / visible-thoughts comparison |
| `deploy.sh` | Enable Vertex AI API and set project |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
