# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Document AI batch processing via GCS (async LRO).

Use batch processing when:
  - Documents exceed 15 pages (synchronous API limit)
  - Processing large volumes (hundreds or thousands of documents)
  - You want async fire-and-poll semantics without blocking a web server

Workflow:
  1. Upload a sample PDF to a GCS input prefix
  2. Submit batch_process_documents (returns a Long-Running Operation)
  3. Poll the LRO until complete (~1-5 minutes for small batches)
  4. Read JSON output files back from the GCS output prefix

Cost: batch OCR billed per page (~$1.50 / 1,000 pages), same as sync OCR.
      Supports up to 500 pages per file and thousands of files per batch.
"""

import json
import os

import requests
from google.api_core import exceptions as gax
from google.cloud import documentai, storage

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = "us"

BUCKET = os.environ.get("DOCAI_GCS_BUCKET", "your-gcs-bucket")
INPUT_PREFIX = "docai/input/"
OUTPUT_PREFIX = "docai/output/"

SAMPLE_PDF_URL = (
    "https://storage.googleapis.com/cloud-samples-data/documentai/loan_form.pdf"
)


def stage_input_pdf(bucket_name: str, pdf_path: str | None = None) -> str:
    """
    Upload a PDF to the GCS input prefix and return the blob name.
    Uses a local file if pdf_path is provided, otherwise downloads the sample PDF.
    """
    gcs = storage.Client(project=PROJECT_ID)
    bucket = gcs.bucket(bucket_name)

    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        filename = os.path.basename(pdf_path)
        print(f"[Batch] Staging local PDF: {pdf_path}")
    else:
        print(f"[Batch] Downloading sample PDF: {SAMPLE_PDF_URL}")
        pdf_bytes = requests.get(SAMPLE_PDF_URL, timeout=60).content
        filename = "loan_form.pdf"
        print(f"[Batch] Downloaded {len(pdf_bytes):,} bytes")

    blob_name = f"{INPUT_PREFIX}{filename}"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(pdf_bytes, content_type="application/pdf")
    print(f"[Batch] Staged: gs://{bucket_name}/{blob_name}")
    return blob_name


def run_batch_process(processor_id: str, bucket_name: str) -> bool:
    """
    Submit a Document AI batch_process_documents request and poll until complete.
    Input: all PDFs under gs://<bucket>/<INPUT_PREFIX>
    Output: JSON files written to gs://<bucket>/<OUTPUT_PREFIX>
    Returns True on success, False on error.
    """
    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
    )

    request = documentai.BatchProcessRequest(
        name=client.processor_path(PROJECT_ID, LOCATION, processor_id),
        input_documents=documentai.BatchDocumentsInputConfig(
            gcs_prefix=documentai.GcsPrefix(
                gcs_uri_prefix=f"gs://{bucket_name}/{INPUT_PREFIX}"
            )
        ),
        document_output_config=documentai.DocumentOutputConfig(
            gcs_output_config=documentai.DocumentOutputConfig.GcsOutputConfig(
                gcs_uri=f"gs://{bucket_name}/{OUTPUT_PREFIX}"
            )
        ),
    )

    try:
        print("[Batch] Submitting batch_process_documents (async LRO) ...")
        operation = client.batch_process_documents(request=request)
        print(f"[Batch] Operation: {operation.operation.name}")
        print("[Batch] Polling until done (can take 1-5 minutes for small batches) ...")
        operation.result(timeout=600)
        print("[Batch] Batch operation complete.")
        return True
    except (gax.PermissionDenied, gax.FailedPrecondition) as e:
        print("\n[Batch] ERROR: Document AI API call failed.")
        print(f"  gcloud services enable documentai.googleapis.com --project={PROJECT_ID}")
        print(f"\nDetails: {e.message if hasattr(e, 'message') else e}")
        return False


def read_batch_output(bucket_name: str) -> list[dict]:
    """
    Read all JSON output files from the GCS output prefix.
    Returns a list of dicts with keys: blob_name, pages, text_length, preview.
    """
    gcs = storage.Client(project=PROJECT_ID)
    results = []

    print(f"\n[Batch] Reading results from gs://{bucket_name}/{OUTPUT_PREFIX}")
    for blob in gcs.list_blobs(bucket_name, prefix=OUTPUT_PREFIX):
        if not blob.name.endswith(".json"):
            continue
        doc_json = json.loads(blob.download_as_bytes())
        text = doc_json.get("text", "")
        pages = doc_json.get("pages", [])
        result = {
            "blob_name": blob.name,
            "pages": len(pages),
            "text_length": len(text),
            "preview": text[:200],
        }
        results.append(result)
        print(f"\n  {blob.name}")
        print(f"    pages: {len(pages)}, text: {len(text):,} chars")
        print(f"    preview: {text[:200]!r}")

    return results
