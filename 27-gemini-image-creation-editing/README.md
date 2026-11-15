# 27. Gemini Image Creation and Editing

> Native image generation and conversational editing using Gemini — generate an image, edit it in a follow-up turn, then analyse the result with multimodal understanding. **GCP Services:** Vertex AI, Gemini 2.5 Flash Image, Gemini 2.5 Flash

## Architecture

```
Text prompt
    │
    ▼
gemini-2.5-flash-image (Chat session)
    │
    ├── Turn 1: Generate ──► gemini_scooter_v1.png
    │           "red vintage scooter..."
    │
    ├── Turn 2: Edit ──────► gemini_scooter_v2_blue.png
    │           "make it blue..." (same chat context)
    │
    └── gemini-2.5-flash (Analysis)
              ├── Description (natural language)
              ├── Object extraction (JSON)
              └── OCR (any visible text)
```

## What this demonstrates

- Gemini native image generation (`gemini-2.5-flash-image`) vs. Imagen — key difference: conversational context
- Conversational editing: no masks, no re-prompting — just describe the change in a follow-up turn
- `response_modalities=["TEXT", "IMAGE"]` — required config for image output
- Inline image bytes extraction from Gemini response parts
- Multimodal analysis with `gemini-2.5-flash`: description, structured JSON object extraction, OCR
- Uploading local image bytes for analysis (no GCS required)

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1

# Authenticate
gcloud auth application-default login
```

> Note: `gemini-2.5-flash-image` is available in `us-central1` / global on Vertex AI. Keep `GOOGLE_CLOUD_LOCATION=us-central1`.

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

Generated images are saved to `output/`.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — generate, edit, then analyse |
| `image_generation.py` | Gemini chat-based image generation and conversational editing |
| `image_analysis.py` | Gemini multimodal analysis: description, object JSON, OCR |
| `requirements.txt` | Python dependencies |
| `deploy.sh` | Enables Vertex AI APIs |

## Cost

| Step | Model | Estimated cost |
|------|-------|---------------|
| Generate (Turn 1) | gemini-2.5-flash-image | ~$0.039 |
| Edit (Turn 2) | gemini-2.5-flash-image | ~$0.039 |
| Analysis (3 calls) | gemini-2.5-flash | ~$0.01 |
| **Total** | | **~$0.09** |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
