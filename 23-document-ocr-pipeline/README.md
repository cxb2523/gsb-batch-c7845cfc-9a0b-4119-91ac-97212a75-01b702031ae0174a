# 23. Document AI OCR Pipeline

> Create a Document AI OCR processor, process a PDF synchronously, and extract text with per-page statistics. **GCP Services:** Document AI (OCR Processor)

> **Requires Document AI API enabled. Run `deploy.sh` first.**

## Architecture

```
[PDF File]  (local or public sample auto-download)
     |
     v
[processor.py]  List processors -> Reuse or Create OCR_PROCESSOR
     |
     | processor_id
     v
[ocr.py]  documentai.ProcessRequest (synchronous, up to 15 pages)
     |
     v
[Document AI OCR Engine]
     |
     v
 doc.text          doc.pages[]
 (full text)       (lines, tokens, paragraphs per page)
     |
     v
[Print extracted text + per-page summary]
```

## What this demonstrates

- Idempotent OCR processor creation — list existing processors and reuse them instead of creating duplicates
- Synchronous PDF processing with the Document AI `process_document` API
- Extracting full text from the returned Document object
- Per-page statistics: line count, token count, paragraph count
- Loading PDFs from local disk or fetching a public sample automatically
- Handling Document AI API errors with actionable remediation messages

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
```

Authenticate:
```bash
gcloud auth application-default login
```

> **Note:** Document AI API must be enabled before running. The `deploy.sh` script handles this.

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Use the auto-downloaded public sample PDF:
python main.py

# Use your own PDF:
python main.py --pdf data/your-document.pdf

# Reuse an existing processor (skip creation):
export DOCAI_PROCESSOR_ID=your-processor-id
python main.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrates processor creation and OCR; accepts --pdf and --processor-id args |
| `processor.py` | Idempotent OCR_PROCESSOR creation — lists existing, creates if absent |
| `ocr.py` | Synchronous PDF OCR — loads bytes, calls Document AI, prints results |
| `data/README_data.txt` | Instructions for adding your own PDF files |
| `requirements.txt` | Python dependencies |
| `deploy.sh` | Enable Document AI API |

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
