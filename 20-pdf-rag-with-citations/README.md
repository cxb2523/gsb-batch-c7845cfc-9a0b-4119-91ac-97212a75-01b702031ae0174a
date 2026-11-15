# 20. PDF RAG with Citations

> Load a PDF (or text file), chunk with overlap, retrieve relevant passages, and generate Gemini answers with mandatory inline [source-N] citations — fully auditable, hallucinated citation ids flagged. **GCP Services:** Vertex AI (Gemini Embedding API, Gemini 2.5 Flash)

## Architecture

```
data/sample_document.txt  (or real PDF via --pdf flag)
          |
          v
  pdf_loader.py ──> load_text_file() / load_pdf_from_url()
                    chunk_text()   ──> overlapping windows (size=1200, overlap=150)
                    load_and_chunk() ──> returns chunks + metadata dict
          |
          v
 citation_rag.py ──> build_index()            ──> embed chunks (RETRIEVAL_DOCUMENT)
                     retrieve_with_ids()       ──> embed question (RETRIEVAL_QUERY)
                                               ──> assign stable source-{original_idx} ids
                     generate_with_citations() ──> Gemini forced to cite [source-N] per claim
                     parse_citations()         ──> detect hallucinated / uncited ids
                     CitedAnswer dataclass     ──> full audit trail
          |
          v
    main.py ──> load → chunk → index → 3 questions → citation audit printout
```

## What this demonstrates

- Overlapping chunking for PDFs: overlap=150/1200 ensures boundary sentences appear complete in at least one chunk
- Stable source ids: uses original chunk index (not rank position) so citations remain valid across reruns
- Forced citation prompt: instructs Gemini to append `[source-N]` after every factual claim — non-negotiable for enterprise RAG
- Hallucination detection: model-invented citation ids (not in the retrieved set) are flagged explicitly
- Full audit trail: per answer — sources retrieved, sources cited, hallucinated ids, uncited sources
- Dual-mode: local text file (no network needed) or real PDF download via `--pdf`

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

# Local text file mode (no PDF download):
python main.py

# Real PDF mode (downloads Gemini 1.5 paper from public GCS):
python main.py --pdf
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — load, chunk, index, run 3 cited RAG questions |
| `pdf_loader.py` | PDF/text loading + overlapping chunking |
| `citation_rag.py` | `CitedAnswer` dataclass, citation-forced generation, hallucination detection |
| `data/sample_document.txt` | ~400-word GCP data engineering best practices doc |
| `deploy.sh` | Enable Vertex AI API and set project |
| `requirements.txt` | Python dependencies (includes `pypdf`, `requests` for PDF mode) |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
