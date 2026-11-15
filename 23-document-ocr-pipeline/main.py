# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 23 — Document AI OCR Pipeline

Workflow:
  1. Create (or reuse) an OCR_PROCESSOR in your GCP project
  2. Load a PDF from disk or download the public sample
  3. Run synchronous OCR and extract text + per-page statistics

Requires Document AI API enabled. Run deploy.sh first.

Usage:
  python main.py                        # uses public sample PDF
  python main.py --pdf path/to/file.pdf # uses your own PDF
  python main.py --processor-id <id>    # skip processor creation, reuse existing
"""

import argparse
import os
import sys

import processor as proc_module
import ocr


def parse_args():
    parser = argparse.ArgumentParser(description="Document AI OCR Pipeline")
    parser.add_argument(
        "--pdf",
        default=None,
        help="Path to a local PDF file. Defaults to a public sample PDF.",
    )
    parser.add_argument(
        "--processor-id",
        default=os.environ.get("DOCAI_PROCESSOR_ID", ""),
        help="Existing Document AI OCR processor ID (skips creation step). "
             "Can also be set via DOCAI_PROCESSOR_ID env var.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 70)
    print("Project 23: Document AI OCR Pipeline")
    print("=" * 70)

    # Step 1: Get or create the OCR processor
    if args.processor_id:
        processor_id = args.processor_id
        print(f"[Main] Using provided processor ID: {processor_id}")
    else:
        print("\n[Main] Step 1: Get or create OCR processor")
        processor_id = proc_module.get_or_create_ocr_processor()
        if not processor_id:
            print("\n[Main] Cannot proceed without a valid processor ID.")
            print("       Run deploy.sh first, then re-run this script.")
            sys.exit(1)

    print(f"\n[Main] Step 2: Run OCR on PDF")
    extracted_text = ocr.run(processor_id, pdf_path=args.pdf)

    if extracted_text:
        print("\n[Main] OCR pipeline complete.")
        print(f"       Total extracted text: {len(extracted_text):,} characters")
    else:
        print("\n[Main] OCR pipeline failed. Check error messages above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
