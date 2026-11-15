# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 28: Vision API Object Detection
Runs three vision tasks on the same GCS image: object detection with bounding
boxes (Gemini), label/landmark detection (Vision API), and OCR (Vision API).

IMPORTANT: Cloud Vision API must be enabled before running Vision API steps.
  gcloud services enable vision.googleapis.com --project=your-project-id
  Or run: ./deploy.sh
"""

import os

from google.api_core import exceptions as gax

from object_detection import get_client, detect_objects, print_detections
from label_detection import (
    get_vision_client,
    detect_labels, detect_landmarks,
    print_labels, print_landmarks,
    LABEL_IMAGE_URI, LANDMARK_IMAGE_URI,
)
from ocr_extraction import (
    text_detection, document_text_detection, print_ocr_results,
    SIGN_IMAGE_URI, DOC_IMAGE_URI,
)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

# Shared image for Gemini object detection
DETECTION_IMAGE_URI = "gs://cloud-samples-data/generative-ai/image/scones.jpg"


def main():
    print("=" * 60)
    print("Project 28: Vision API Object Detection")
    print("=" * 60)
    print(f"Project : {PROJECT_ID}")
    print(f"Location: {LOCATION}\n")

    # --- Step 1: Object detection with bounding boxes (Gemini) ---------------
    print("--- Step 1: Object detection with bounding boxes (Gemini) ---")
    print(f"Image: {DETECTION_IMAGE_URI}\n")
    gen_client = get_client(PROJECT_ID, LOCATION)
    try:
        detections = detect_objects(gen_client, DETECTION_IMAGE_URI)
        print_detections(detections)
    except Exception as e:
        print(f"Object detection error: {e}")
    print()

    # --- Step 2: Label and landmark detection (Vision API) -------------------
    print("--- Step 2: Label + landmark detection (Cloud Vision API) ---")
    print("NOTE: Requires vision.googleapis.com to be enabled.\n")
    try:
        vision_client = get_vision_client()

        labels = detect_labels(vision_client, LABEL_IMAGE_URI)
        print_labels(labels, LABEL_IMAGE_URI)

        print()
        landmarks = detect_landmarks(vision_client, LANDMARK_IMAGE_URI)
        print_landmarks(landmarks, LANDMARK_IMAGE_URI)

    except (gax.PermissionDenied, gax.FailedPrecondition) as e:
        print(f"\nERROR: Vision API not accessible.")
        print(f"Run: gcloud services enable vision.googleapis.com --project={PROJECT_ID}")
        print(f"Details: {e}")
    except RuntimeError as e:
        print(f"\nERROR: {e}")
    print()

    # --- Step 3: OCR (Vision API) --------------------------------------------
    print("--- Step 3: OCR — text_detection + document_text_detection ---")
    print("NOTE: Requires vision.googleapis.com to be enabled.\n")
    try:
        vision_client = get_vision_client()

        sign_text = text_detection(vision_client, SIGN_IMAGE_URI)
        doc_result = document_text_detection(vision_client, DOC_IMAGE_URI)
        print_ocr_results(sign_text, doc_result)

    except (gax.PermissionDenied, gax.FailedPrecondition) as e:
        print(f"\nERROR: Vision API not accessible.")
        print(f"Run: gcloud services enable vision.googleapis.com --project={PROJECT_ID}")
        print(f"Details: {e}")
    except RuntimeError as e:
        print(f"\nERROR: {e}")

    print()
    print("=" * 60)
    print("Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
