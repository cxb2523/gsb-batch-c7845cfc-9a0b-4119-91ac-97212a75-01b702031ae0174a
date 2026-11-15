# 28. Vision API Object Detection

> Three vision intelligence tasks on the same images: Gemini-based object detection with bounding boxes, Cloud Vision API label/landmark detection, and Vision API OCR. **GCP Services:** Vertex AI, Gemini 2.5 Flash, Cloud Vision API

> **Important:** Cloud Vision API (`vision.googleapis.com`) was **not enabled** during initial development of the Vision API modules. Run `./deploy.sh` before executing Steps 2 and 3, or enable manually:
> ```bash
> gcloud services enable vision.googleapis.com --project=your-project-id
> ```
> Step 1 (Gemini object detection) works without the Vision API.

## Architecture

```
GCS public images
        │
        ├── Step 1: Gemini 2.5 Flash ──► Object detection + bounding boxes (JSON)
        │          (no Vision API needed)
        │
        ├── Step 2: Cloud Vision API
        │          ├── Label detection  ──► Top-N labels with confidence scores
        │          └── Landmark detect  ──► Landmark name + lat/lng
        │
        └── Step 3: Cloud Vision API OCR
                   ├── text_detection          ──► Sparse/photo text
                   └── document_text_detection ──► Dense doc text + block structure
```

## What this demonstrates

- Gemini bounding box detection: normalized 0-1000 coordinate system + pixel mapping
- Structured JSON output with `response_schema` — forces valid, constrained JSON from Gemini
- Cloud Vision API label detection: fixed-taxonomy tagging, high-volume use case
- Landmark detection with geographic coordinates (lat/lng)
- Two OCR modes: `text_detection` (sparse photo text) vs. `document_text_detection` (dense/structured)
- When to use Vision API vs. Gemini vs. Document AI for text extraction

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1

# Authenticate
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

The `deploy.sh` script enables `aiplatform.googleapis.com` and `vision.googleapis.com`.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — runs all three detection steps in sequence |
| `object_detection.py` | Gemini bounding box detection with JSON schema constraint |
| `label_detection.py` | Vision API label detection and landmark detection |
| `ocr_extraction.py` | Vision API `text_detection` and `document_text_detection` |
| `requirements.txt` | Python dependencies |
| `deploy.sh` | Enables Vertex AI + Cloud Vision APIs |

## OCR Decision Guide

| Use case | Recommended tool |
|----------|-----------------|
| Signs, labels, incidental text in photos | Vision API `text_detection` |
| Scanned documents, dense printed text | Vision API `document_text_detection` |
| Multi-page PDFs, tables, form fields | Document AI |
| Text + reasoning / summarization | Gemini multimodal |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
