# 25. Batch Document Processing + Layout-Aware RAG Chunking

> Async batch Document AI processing from GCS combined with layout-aware PDF chunking for enterprise RAG ingestion pipelines. **GCP Services:** Document AI (Batch Processing, Layout Parser), Cloud Storage

> **Requires Document AI API enabled. Run `deploy.sh` first.**

## Architecture

```
DEMO 1: Batch GCS Processing
=====================================
[PDF Files]
     |
     v
[GCS Input Prefix]  gs://bucket/docai/input/
     |
     v
[Document AI batch_process_documents]  (async Long-Running Operation)
     |
     | LRO polls until complete
     v
[GCS Output Prefix]  gs://bucket/docai/output/*.json
     |
     v
[Read JSON results: text, pages, preview]


DEMO 2: Layout-Aware Chunking for RAG
=====================================
[PDF File]
     |
     v
[LAYOUT_PARSER_PROCESSOR]
  chunking_config:
    chunk_size=500
    include_ancestor_headings=True
     |
     v
Chunks respecting document structure:
  - Headings / sections
  - Paragraphs (never split mid-paragraph)
  - Tables (never split mid-row)
  - Heading ancestry as context prefix
     |
     v
[RAG Records]  -> embed content -> upsert to vector store
```

## What this demonstrates

- Batch async Document AI processing: submit a GCS-to-GCS batch job, poll the LRO, and read JSON output — the production pattern for large document volumes
- Layout Parser with chunking config: structure-aware PDF splitting that respects headings, paragraphs, and tables
- `include_ancestor_headings=True`: each chunk carries its section heading ancestry as a context prefix, dramatically improving RAG retrieval precision on long documents
- Converting Layout Parser chunks to RAG-ready records with metadata (chunk_id, page span, char count)
- Why layout-aware chunking outperforms naive fixed-size splitting for enterprise PDFs (contracts, policies, manuals)

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export DOCAI_GCS_BUCKET=your-gcs-bucket
```

Authenticate:
```bash
gcloud auth application-default login
```

> **Note:** Document AI API must be enabled. The GCS bucket must be in the same project.

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

Then create processor(s) in the GCP Console:
- Batch demo: Document AI > Create Processor > **OCR Processor**
- Layout demo: Document AI > Create Processor > **Layout Parser**

```bash
export DOCAI_PROCESSOR_ID=your-processor-id
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run both demos:
python main.py

# Batch GCS processing only (OCR or Layout Parser processor):
python main.py --mode batch

# Layout-aware chunking only:
python main.py --mode layout

# Use your own PDF:
python main.py --pdf data/my-document.pdf
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrates both demos; --mode flag selects batch, layout, or both |
| `batch_processor.py` | Batch GCS processing — stage input, submit LRO, poll, read JSON output |
| `layout_chunker.py` | Layout Parser chunking — structure-aware chunks for RAG ingestion |
| `requirements.txt` | Python dependencies |
| `deploy.sh` | Enable Document AI API, create GCS bucket, print setup instructions |

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
