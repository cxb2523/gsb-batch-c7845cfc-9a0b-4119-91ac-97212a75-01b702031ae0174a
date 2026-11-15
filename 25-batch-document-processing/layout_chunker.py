# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Document AI Layout Parser — layout-aware chunking for RAG ingestion.

Why layout-aware chunking beats naive fixed-size splitting for enterprise docs:

- Chunks respect document structure (headings, paragraphs, tables, lists),
  so a table row is never split across two embeddings.
- Each chunk carries its heading ancestry as context, dramatically improving
  retrieval precision on long contracts, policies, and technical manuals.
- Naive character splitting mixes unrelated sections into one chunk and
  destroys table semantics — the primary cause of low-quality RAG answers
  on enterprise documents.

Cost: ~$10 per 1,000 pages for the Layout Parser (paid once per document
version as an ingestion-time cost in the RAG pipeline).

Requires: a LAYOUT_PARSER_PROCESSOR in your project.
Create via Console: Document AI > Create Processor > Layout Parser
"""

import os

import requests
from google.api_core import exceptions as gax
from google.cloud import documentai

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = "us"

SAMPLE_PDF_URL = (
    "https://storage.googleapis.com/cloud-samples-data/documentai/loan_form.pdf"
)

# Target tokens per chunk. Include ancestor headings so every chunk carries
# its section context (critical for retrieval accuracy).
CHUNK_SIZE = 500
INCLUDE_ANCESTOR_HEADINGS = True


def chunk_pdf(processor_id: str, pdf_path: str | None = None) -> list[dict] | None:
    """
    Run the Layout Parser with chunking config on a PDF.
    Returns a list of chunk dicts: [{"chunk_id": ..., "pages": ..., "content": ...}, ...]
    or None on error.
    """
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        print(f"[LayoutChunker] Loaded local PDF: {pdf_path} ({len(pdf_bytes):,} bytes)")
    else:
        print(f"[LayoutChunker] Downloading sample PDF: {SAMPLE_PDF_URL}")
        pdf_bytes = requests.get(SAMPLE_PDF_URL, timeout=60).content
        print(f"[LayoutChunker] Downloaded {len(pdf_bytes):,} bytes")

    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
    )

    process_options = documentai.ProcessOptions(
        layout_config=documentai.ProcessOptions.LayoutConfig(
            chunking_config=documentai.ProcessOptions.LayoutConfig.ChunkingConfig(
                chunk_size=CHUNK_SIZE,
                include_ancestor_headings=INCLUDE_ANCESTOR_HEADINGS,
            )
        )
    )

    try:
        print(f"[LayoutChunker] Processing with LAYOUT_PARSER_PROCESSOR {processor_id} ...")
        result = client.process_document(
            request=documentai.ProcessRequest(
                name=client.processor_path(PROJECT_ID, LOCATION, processor_id),
                raw_document=documentai.RawDocument(
                    content=pdf_bytes, mime_type="application/pdf"
                ),
                process_options=process_options,
            )
        )
    except (gax.PermissionDenied, gax.FailedPrecondition) as e:
        print("\n[LayoutChunker] ERROR: Document AI API call failed.")
        print(f"  gcloud services enable documentai.googleapis.com --project={PROJECT_ID}")
        print(f"\nDetails: {e.message if hasattr(e, 'message') else e}")
        return None

    raw_chunks = result.document.chunked_document.chunks
    chunks = []
    for chunk in raw_chunks:
        pages = [f"{ps.page_start}-{ps.page_end}" for ps in chunk.page_span[:1]]
        chunks.append({
            "chunk_id": chunk.chunk_id,
            "pages": ", ".join(pages) if pages else "?",
            "content": chunk.content,
        })
    return chunks


def print_chunks(chunks: list[dict], preview_chars: int = 400) -> None:
    """Print a summary of all layout-aware chunks."""
    print(f"\n=== LAYOUT-AWARE CHUNKS ({len(chunks)} total) ===")
    for chunk in chunks:
        print(f"\n--- Chunk {chunk['chunk_id']} (pages {chunk['pages']}) ---")
        print(chunk["content"][:preview_chars])
        if len(chunk["content"]) > preview_chars:
            print(f"  ... [{len(chunk['content']) - preview_chars} more chars]")


def chunks_to_rag_records(chunks: list[dict]) -> list[dict]:
    """
    Convert chunks to records ready for embedding and vector store ingestion.
    Each record has: chunk_id, pages, content (the text to embed), char_count.
    """
    return [
        {
            "chunk_id": c["chunk_id"],
            "pages": c["pages"],
            "content": c["content"],
            "char_count": len(c["content"]),
        }
        for c in chunks
    ]
