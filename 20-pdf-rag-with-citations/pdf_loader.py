# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
PDF loading, text extraction, and chunking for RAG pipelines.
Source: 03_rag_over_pdf.py

Supports:
  - Loading a real PDF from a URL (e.g., a research paper from GCS)
  - Loading a plain text file as a PDF substitute for local demos
  - Overlapping character-level chunking (the standard pattern for PDFs)

Why overlap matters for PDFs: PDF text extraction is imperfect. Headers,
footers, and column layouts often fragment sentences across page breaks.
An overlap of 10-15% of chunk_size ensures that context crossing a boundary
appears complete in at least one chunk.
"""

import io
import os
from pathlib import Path
from typing import Optional

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

# Default values matching the source sample
DEFAULT_CHUNK_SIZE = 1200
DEFAULT_OVERLAP = 150
DEFAULT_MAX_PAGES = 15

# Public PDF for demo (Gemini 1.5 paper)
DEMO_PDF_URL = "https://storage.googleapis.com/cloud-samples-data/generative-ai/pdf/2403.05530.pdf"


def chunk_text(text: str, size: int = DEFAULT_CHUNK_SIZE,
               overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """
    Split text into overlapping character windows.

    Step = size - overlap. Each chunk starts `step` characters after the previous.
    The overlap ensures that a sentence cut at the end of chunk N appears
    complete at the start of chunk N+1 — critical for answers that straddle
    a chunk boundary.

    Only non-whitespace chunks are returned.
    """
    step = size - overlap
    return [
        text[i: i + size]
        for i in range(0, len(text), step)
        if text[i: i + size].strip()
    ]


def load_pdf_from_url(url: str, max_pages: int = DEFAULT_MAX_PAGES) -> tuple[str, int, int]:
    """
    Download a PDF from a public URL and extract text using pypdf.

    Returns (text, pages_read, total_pages).
    Requires: pip install pypdf requests
    """
    try:
        import requests
        from pypdf import PdfReader
    except ImportError as e:
        raise ImportError(
            f"Missing dependency: {e}. Install with: pip install pypdf requests"
        ) from e

    print(f"Downloading PDF: {url}")
    pdf_bytes = requests.get(url, timeout=60).content
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = reader.pages[:max_pages]
    text = "\n".join(page.extract_text() or "" for page in pages)
    return text, len(pages), len(reader.pages)


def load_text_file(path: Path) -> str:
    """
    Load a plain text file as a PDF substitute.

    Use this for local demos when you don't want to download a real PDF.
    The text is treated as a single continuous document and chunked the same way.
    """
    return path.read_text(encoding="utf-8")


def load_and_chunk(
    text_path: Optional[Path] = None,
    pdf_url: Optional[str] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[list[str], dict]:
    """
    Load a document (text file or PDF URL) and return chunks + metadata.

    Exactly one of text_path or pdf_url must be provided.
    Returns (chunks, metadata_dict).
    """
    if text_path is not None:
        text = load_text_file(text_path)
        source = str(text_path.name)
        meta = {"source": source, "chars": len(text), "type": "text"}
    elif pdf_url is not None:
        text, pages_read, total_pages = load_pdf_from_url(pdf_url, max_pages)
        source = pdf_url.split("/")[-1]
        meta = {
            "source": source,
            "chars": len(text),
            "pages_read": pages_read,
            "total_pages": total_pages,
            "type": "pdf",
        }
    else:
        raise ValueError("Provide either text_path or pdf_url.")

    chunks = chunk_text(text, size=chunk_size, overlap=overlap)
    meta["chunks"] = len(chunks)
    meta["chunk_size"] = chunk_size
    meta["overlap"] = overlap

    return chunks, meta


def print_load_summary(meta: dict) -> None:
    """Print a summary of the loaded document."""
    print(f"Source      : {meta['source']}")
    print(f"Type        : {meta['type']}")
    if "pages_read" in meta:
        print(f"Pages read  : {meta['pages_read']} of {meta['total_pages']}")
    print(f"Chars       : {meta['chars']:,}")
    print(f"Chunks      : {meta['chunks']}  "
          f"(size={meta['chunk_size']}, overlap={meta['overlap']})")
