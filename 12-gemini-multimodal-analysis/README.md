# 12. Gemini Multimodal Analysis

> Analyze images, PDFs, and video/audio using Gemini's native multimodal understanding — no OCR, no Speech-to-Text, no Video Intelligence APIs needed. **GCP Services:** Vertex AI (Gemini API), Cloud Storage

## Architecture

```
GCS Assets (images / PDFs / video / audio)
    │
    ▼
Part.from_uri(gcs_uri, mime_type)      ← zero-copy: Vertex AI reads GCS directly
Part.from_bytes(data, mime_type)       ← inline bytes for user uploads
    │
    ▼
genai.Client → models.generate_content()
    │
    ▼
Structured text response (description / summary / transcription / extraction)
```

## What this demonstrates

- Image analysis from a GCS URI (`Part.from_uri`) — zero-copy, no bytes transmitted through your machine
- Image analysis from local bytes (`Part.from_bytes`) — for user uploads or files not yet on GCS
- PDF document summarization and targeted field extraction — native PDF reading, no parser needed
- Video description — frame + audio track sampling, token cost scales with clip length
- Audio transcription and summarization — no separate Speech-to-Text API required
- Mixed content lists: text prompts combined freely with media parts

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
gcloud auth application-default login
```

The sample assets used (image, PDF, video, audio) are publicly hosted in Google's Cloud Storage sample buckets — no additional GCS setup needed.

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
| `main.py` | Orchestrates image, PDF, and media analysis in sequence |
| `image_analysis.py` | GCS URI and local bytes image analysis patterns |
| `document_analysis.py` | PDF summarization and targeted extraction |
| `media_analysis.py` | Video description and audio transcription |
| `data/sample_prompt.txt` | Sample prompts for each modality |
| `deploy.sh` | Enable Vertex AI API and set project |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
