# 11. Gemini Text Generation Basics

> Four foundational patterns for calling Gemini on Vertex AI: basic generation, generation config tuning, streaming responses, and token counting. **GCP Services:** Vertex AI (Gemini API)

## Architecture

```
Your Script
    │
    ▼
genai.Client (Vertex AI)
    │
    ├── models.generate_content()       ← basic call, generation config
    ├── models.generate_content_stream() ← streaming (chunk-by-chunk)
    └── models.count_tokens()           ← pre-flight token count
                                        ← usage_metadata post-flight
```

## What this demonstrates

- Single synchronous `generate_content` call — baseline auth and model access check
- `GenerateContentConfig` knobs: `temperature`, `top_p`, `max_output_tokens`, `stop_sequences`, `seed`, `candidate_count`
- Streaming via `generate_content_stream` — time-to-first-chunk latency measurement
- Pre-flight token counting with `count_tokens` to gate cost before committing a call
- Post-flight `usage_metadata` inspection: prompt, candidate, thinking, and total tokens
- Gemini 2.5 thinking tokens — billed as output, reported separately in `thoughts_token_count`

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
| `main.py` | Orchestrates all four patterns in sequence |
| `generation.py` | Basic `generate_content` call + `GenerateContentConfig` demo |
| `streaming.py` | Streaming response with time-to-first-chunk timing |
| `tokens.py` | Pre-flight `count_tokens` + post-flight `usage_metadata` |
| `deploy.sh` | Enable Vertex AI API and set project |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
