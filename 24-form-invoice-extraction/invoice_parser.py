# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Document AI Invoice Parser — structured entity extraction from invoices.

The Invoice Parser extracts structured fields from supplier invoices:
supplier name, invoice ID, invoice date, due date, total amount, tax amount,
currency, and nested line-item entities (description, quantity, unit price,
total price). No training or custom model required — it generalises across
invoice layouts out of the box.

Cost: ~$10 per 1,000 pages for the Invoice Parser processor.

Requires: an INVOICE_PROCESSOR in your project.
Create via Console: Document AI > Create Processor > Invoice Parser
Note: some organisations require allowlisting before specialized parsers
(Invoice, Expense, etc.) are available to create.
"""

import os

import requests
from google.api_core import exceptions as gax
from google.cloud import documentai

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = "us"

SAMPLE_INVOICE_URL = (
    "https://storage.googleapis.com/cloud-samples-data/documentai/invoice.pdf"
)

# Entity types considered headline / most important for AP automation
HEADLINE_FIELDS = {
    "supplier_name", "total_amount", "due_date", "invoice_date",
    "invoice_id", "currency", "net_amount", "total_tax_amount",
}


def parse_invoice(processor_id: str, pdf_path: str | None = None) -> documentai.Document | None:
    """
    Run the Invoice Parser on a PDF from disk or the public sample invoice.
    Returns the Document AI Document object, or None on error.
    """
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        print(f"[InvoiceParser] Loaded local PDF: {pdf_path} ({len(pdf_bytes):,} bytes)")
    else:
        print(f"[InvoiceParser] Downloading sample invoice: {SAMPLE_INVOICE_URL}")
        pdf_bytes = requests.get(SAMPLE_INVOICE_URL, timeout=60).content
        print(f"[InvoiceParser] Downloaded {len(pdf_bytes):,} bytes")

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
        print("\n[InvoiceParser] ERROR: Document AI API call failed.")
        print(f"  gcloud services enable documentai.googleapis.com --project={PROJECT_ID}")
        print(f"\nDetails: {e.message if hasattr(e, 'message') else e}")
        return None


def print_invoice_results(doc: documentai.Document) -> list[dict]:
    """
    Print and return all extracted invoice entities.
    Headline fields (supplier, total, dates, etc.) are marked with *.
    Line items and their nested properties are printed indented.
    Returns a list of entity dicts for programmatic use.
    """
    entities_out = []
    print(f"\n=== INVOICE ENTITIES ({len(doc.entities)} found) ===")

    if not doc.entities:
        print("  (No entities returned — verify the processor is an Invoice Parser,")
        print("   not a Form Parser or OCR Processor.)")
        return entities_out

    for entity in doc.entities:
        marker = "*" if entity.type_ in HEADLINE_FIELDS else " "
        value = entity.normalized_value.text or entity.mention_text
        print(f" {marker}[{entity.confidence:.2f}] {entity.type_:25s} = {value}")
        entities_out.append({
            "type": entity.type_,
            "value": value,
            "confidence": entity.confidence,
            "is_headline": entity.type_ in HEADLINE_FIELDS,
        })

        # Line items carry nested properties: description, quantity, unit_price, amount
        if entity.type_ == "line_item" and entity.properties:
            for prop in entity.properties:
                pval = prop.normalized_value.text or prop.mention_text
                print(f"      [{prop.confidence:.2f}] {prop.type_:30s} = {pval}")

    return entities_out
