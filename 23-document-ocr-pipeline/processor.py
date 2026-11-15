# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Document AI OCR Processor creation (idempotent).

Lists existing processors and reuses an OCR_PROCESSOR if one already exists;
creates a new one otherwise. Run this once per project before running the OCR
pipeline.

Cost note: Creating a processor is free. You pay per page processed (~$1.50
per 1,000 pages for Enterprise Document OCR at the time of writing).

Requires: Document AI API enabled.
  gcloud services enable documentai.googleapis.com
"""

import os

from google.api_core import exceptions as gax
from google.cloud import documentai

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = "us"   # Document AI processor location: "us" or "eu"

PROCESSOR_DISPLAY_NAME = "gcp-hub-ocr-processor"
PROCESSOR_TYPE = "OCR_PROCESSOR"


def get_or_create_ocr_processor() -> str | None:
    """
    Idempotent: return the processor ID of an existing OCR_PROCESSOR in the
    project, or create a new one and return its ID.
    Returns None if the API is not enabled or access is denied.
    """
    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
    )
    parent = client.common_location_path(PROJECT_ID, LOCATION)

    try:
        print(f"[Processor] Listing processors in project {PROJECT_ID} ...")
        for proc in client.list_processors(parent=parent):
            if proc.type_ == PROCESSOR_TYPE:
                processor_id = proc.name.split("/")[-1]
                print(f"[Processor] Reusing existing OCR processor (idempotent):")
                print(f"  Display name : {proc.display_name}")
                print(f"  Processor ID : {processor_id}")
                return processor_id

        print(f"[Processor] No {PROCESSOR_TYPE} found. Creating '{PROCESSOR_DISPLAY_NAME}' ...")
        proc = client.create_processor(
            parent=parent,
            processor=documentai.Processor(
                display_name=PROCESSOR_DISPLAY_NAME,
                type_=PROCESSOR_TYPE,
            ),
        )
        processor_id = proc.name.split("/")[-1]
        print(f"[Processor] Created OCR processor:")
        print(f"  Full name    : {proc.name}")
        print(f"  Processor ID : {processor_id}")
        return processor_id

    except (gax.PermissionDenied, gax.FailedPrecondition) as e:
        print("\n[Processor] ERROR: Document AI API call failed (API likely not enabled).")
        print("Run deploy.sh first to enable the Document AI API:")
        print(f"  gcloud services enable documentai.googleapis.com --project={PROJECT_ID}")
        print(f"\nDetails: {e.message if hasattr(e, 'message') else e}")
        return None
