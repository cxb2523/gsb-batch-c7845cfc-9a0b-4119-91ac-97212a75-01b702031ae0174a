# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Document AI Form Parser — key-value pair and table extraction.

The Form Parser identifies structured key-value pairs and tables in scanned
or digital forms (job applications, intake forms, registration sheets, etc.).
It returns field names, field values, and confidence scores without requiring
any training data — it generalises across form layouts.

Cost: ~$30 per 1,000 pages (pricier than plain OCR because it returns
structured key/value data and tables).

Requires: a FORM_PARSER_PROCESSOR in your project.
Create via Console: Document AI > Create Processor > Form Parser
Or set type_="FORM_PARSER_PROCESSOR" in processor creation code.
"""

import os

import requests
from google.api_core import exceptions as gax
from google.cloud import documentai

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = "us"

SAMPLE_FORM_URL = (
    "https://storage.googleapis.com/cloud-samples-data/documentai/form.pdf"
)


def _layout_text(layout: object, full_text: str) -> str:
    """Reassemble the text for a layout element from its text anchor segments."""
    return "".join(
        full_text[int(seg.start_index): int(seg.end_index)]
        for seg in layout.text_anchor.text_segments
    ).strip()


def parse_form(processor_id: str, pdf_path: str | None = None) -> documentai.Document | None:
    """
    Run the Form Parser on a PDF from disk or the public sample.
    Returns the Document AI Document object, or None on error.
    """
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        print(f"[FormParser] Loaded local PDF: {pdf_path} ({len(pdf_bytes):,} bytes)")
    else:
        print(f"[FormParser] Downloading sample form: {SAMPLE_FORM_URL}")
        pdf_bytes = requests.get(SAMPLE_FORM_URL, timeout=60).content
        print(f"[FormParser] Downloaded {len(pdf_bytes):,} bytes")

    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
    )

    try:
        result = client.process_document(
            request=documentai.ProcessRequest(
                name=client.processor_path(PROJECT_ID, LOCATION, processor_id),
                raw_document=documentai.RawDocument(
                    content=pdf_bytes, mime_type="application/pdf"
                ),
            )
        )
        return result.document
    except (gax.PermissionDenied, gax.FailedPrecondition) as e:
        print("\n[FormParser] ERROR: Document AI API call failed.")
        print(f"  gcloud services enable documentai.googleapis.com --project={PROJECT_ID}")
        print(f"\nDetails: {e.message if hasattr(e, 'message') else e}")
        return None


def print_form_results(doc: documentai.Document) -> list[dict]:
    """
    Print and return all key-value pairs and tables from the form.
    Returns a list of dicts: [{"key": ..., "value": ..., "confidence": ...}, ...]
    """
    fields = []
    print(f"\n=== KEY/VALUE PAIRS ({len(doc.pages)} pages) ===")
    for page in doc.pages:
        for field in page.form_fields:
            key = _layout_text(field.field_name, doc.text)
            value = _layout_text(field.field_value, doc.text)
            conf = field.field_name.confidence
            print(f"  [{conf:.2f}] {key!r:40s} -> {value!r}")
            fields.append({"key": key, "value": value, "confidence": conf})

    print("\n=== TABLES ===")
    for page in doc.pages:
        for t_idx, table in enumerate(page.tables):
            print(
                f"  Page {page.page_number}, table {t_idx + 1}: "
                f"{len(table.header_rows)} header rows, "
                f"{len(table.body_rows)} body rows"
            )
            for row in list(table.header_rows) + list(table.body_rows)[:3]:
                cells = [_layout_text(c.layout, doc.text) for c in row.cells]
                print(f"    | {' | '.join(cells)} |")

    return fields
