# 19. RAG Quickstart: In-Memory Pipeline

> The canonical minimal RAG loop — chunk a knowledge base, embed with Vertex AI, retrieve by cosine similarity, and generate grounded answers with Gemini. Zero vector database. Every stage inspectable. **GCP Services:** Vertex AI (Gemini Embedding API, Gemini 2.5 Flash)

## Architecture

```
data/knowledge_base.txt  (BigQuery article, ~500 words)
          |
          v
    chunker.py ──> fixed_size()              ──> 200-char windows
                   overlapping()             ──> sliding window with overlap
                   by_separator()            ──> paragraph-aware split
                   by_separator_with_max()   ──> recommended: separator + fallback
          |
          v
      rag.py ──> build_index()      ──> embed chunks (RETRIEVAL_DOCUMENT)
                 retrieve()         ──> embed question (RETRIEVAL_QUERY) + cosine rank
                 generate_answer()  ──> Gemini 2.5 Flash grounded on context
          |
          v
    main.py ──> compare strategies → build index → run 3 questions → print results
```

## What this demonstrates

- Four chunking strategies with trade-off analysis (fixed, overlapping, separator, hybrid)
- Why separator chunking outperforms fixed-size for structured text
- Asymmetric task types: `RETRIEVAL_DOCUMENT` for chunks, `RETRIEVAL_QUERY` for questions
- Grounding instruction: why "Answer using ONLY the context" prevents hallucination
- Full RAG pipeline in ~100 lines of Python with no vector database dependency
- When to graduate from in-memory to a vector database (corpus > 100K vectors)

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
| `main.py` | Entry point — chunking comparison + 3-question RAG demo |
| `chunker.py` | Fixed, overlapping, separator, and hybrid chunking strategies |
| `rag.py` | `build_index()`, `retrieve()`, `generate_answer()`, `run_rag()` pipeline |
| `data/knowledge_base.txt` | ~500-word BigQuery knowledge base article used as RAG corpus |
| `deploy.sh` | Enable Vertex AI API and set project |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
