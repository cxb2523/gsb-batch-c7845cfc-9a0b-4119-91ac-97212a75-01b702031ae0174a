# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Synchronous PDF OCR using Document AI.

Reads a PDF from disk (or downloads a public sample), sends it to a Document AI
OCR_PROCESSOR, and returns the full extracted text plus per-page statistics.

Sync API limit: up to 15 pages per request. For larger documents use the
batch API (see project 25-batch-document-processing).

Cost: ~$1.50 per 1,000 pages for Enterprise Document OCR (sync).
"""

import os

import requests
from google.api_core import exceptions as gax
from google.cloud import documentai

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = "us"

# Public sample PDF from Google's cloud-samples-data bucket.
# Replace with your own PDF path via the pdf_path argument to run().
SAMPLE_PDF_URL = (
    "https://storage.googleapis.com/cloud-samples-data/documentai/loan_form.pdf"
)


def load_pdf_bytes(pdf_path: str | None = None) -> bytes:
    """
    Load PDF bytes from a local file path, or download the public sample PDF.
    pdf_path: local filesystem path to a PDF file, or None to use the sample.
    """
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            data = f.read()
        print(f"[OCR] Loaded local PDF: {pdf_path} ({len(data):,} bytes)")
        return data

    print(f"[OCR] Downloading sample PDF: {SAMPLE_PDF_URL}")
    data = requests.get(SAMPLE_PDF_URL, timeout=60).content
    print(f"[OCR] Downloaded {len(data):,} bytes")
    return data


def ocr_pdf(processor_id: str, pdf_bytes: bytes) -> documentai.Document | None:
    """
    Run synchronous OCR on the provided PDF bytes using the given processor ID.
    Returns the Document AI Document object, or None on API error.
    """
    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
    )
    name = client.processor_path(PROJECT_ID, LOCATION, processor_id)

    try:
        print(f"[OCR] Processing with processor {processor_id} ...")
        result = client.process_document(
            request=documentai.ProcessRequest(
                name=name,
                raw_document=documentai.RawDocument(
                    content=pdf_bytes,
                    mime_type="application/pdf",
                ),
            )
        )
        return result.document
    except (gax.PermissionDenied, gax.FailedPrecondition) as e:
        print("\n[OCR] ERROR: Document AI API call failed.")
        print("Ensure the Document AI API is enabled and the processor ID is valid.")
        print(f"  gcloud services enable documentai.googleapis.com --project={PROJECT_ID}")
        print(f"\nDetails: {e.message if hasattr(e, 'message') else e}")
        return None


def print_ocr_results(doc: documentai.Document) -> None:
    """Print a summary and text preview from an OCR Document result."""
    print("\n=== OCR RESULT ===")
    print(f"Pages detected : {len(doc.pages)}")
    print(f"Text length    : {len(doc.text):,} characters")

    print("\n--- Extracted text (first 1,000 chars) ---")
    print(doc.text[:1000])

    print("\n--- Per-page summary ---")
    for page in doc.pages:
        print(
            f"  Page {page.page_number}: "
            f"{len(page.lines)} lines, {len(page.tokens)} tokens, "
            f"{len(page.paragraphs)} paragraphs"
        )


def run(processor_id: str, pdf_path: str | None = None) -> str | None:
    """
    End-to-end OCR: load PDF → process → print results.
    Returns the extracted text, or None on failure.
    """
    pdf_bytes = load_pdf_bytes(pdf_path)
    doc = ocr_pdf(processor_id, pdf_bytes)
    if doc is None:
        return None
    print_ocr_results(doc)
    return doc.text
