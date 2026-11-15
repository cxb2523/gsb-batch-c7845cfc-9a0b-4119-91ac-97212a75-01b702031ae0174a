# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 24 — Document AI Form Parser + Invoice Parser

Demonstrates:
  1. Form Parser — extract key-value pairs and tables from a form PDF
  2. Invoice Parser — extract structured invoice entities (supplier, totals,
     line items) from an invoice PDF

Both parsers require separate processor IDs because they are different
processor types in Document AI.

Requires Document AI API enabled. Run deploy.sh first.

Usage:
  python main.py                              # runs both demos with sample PDFs
  python main.py --mode form                  # form parsing only
  python main.py --mode invoice               # invoice parsing only
  python main.py --form-processor-id <id>     # skip form processor lookup
  python main.py --invoice-processor-id <id>  # skip invoice processor lookup
  python main.py --pdf data/my-form.pdf --mode form
"""

import argparse
import os
import sys

import form_parser
import invoice_parser


def parse_args():
    parser = argparse.ArgumentParser(description="Document AI Form + Invoice Extraction")
    parser.add_argument(
        "--mode",
        choices=["form", "invoice", "both"],
        default="both",
        help="Which parser to run (default: both)",
    )
    parser.add_argument(
        "--form-processor-id",
        default=os.environ.get("DOCAI_FORM_PROCESSOR_ID", ""),
        help="FORM_PARSER_PROCESSOR ID. Can also be set via DOCAI_FORM_PROCESSOR_ID env var.",
    )
    parser.add_argument(
        "--invoice-processor-id",
        default=os.environ.get("DOCAI_INVOICE_PROCESSOR_ID", ""),
        help="INVOICE_PROCESSOR ID. Can also be set via DOCAI_INVOICE_PROCESSOR_ID env var.",
    )
    parser.add_argument(
        "--pdf",
        default=None,
        help="Path to a local PDF file (used for whichever parser is selected). "
             "Defaults to a public sample for each parser.",
    )
    return parser.parse_args()


def run_form_demo(processor_id: str, pdf_path: str | None = None):
    print("\n" + "=" * 70)
    print("DEMO 1: Form Parser — Key/Value Pairs + Tables")
    print("=" * 70)

    if not processor_id:
        print("\nERROR: FORM_PARSER_PROCESSOR ID is required.")
        print("Create a Form Parser processor in the GCP Console:")
        print("  Document AI > Create Processor > Form Parser")
        print("Then set: export DOCAI_FORM_PROCESSOR_ID=<id>")
        print("  or pass: --form-processor-id <id>")
        return None

    doc = form_parser.parse_form(processor_id, pdf_path)
    if doc is None:
        return None
    fields = form_parser.print_form_results(doc)
    print(f"\n[Main] Form parsing complete: {len(fields)} key-value pairs extracted.")
    return fields


def run_invoice_demo(processor_id: str, pdf_path: str | None = None):
    print("\n" + "=" * 70)
    print("DEMO 2: Invoice Parser — Structured Entity Extraction")
    print("=" * 70)

    if not processor_id:
        print("\nERROR: INVOICE_PROCESSOR ID is required.")
        print("Create an Invoice Parser processor in the GCP Console:")
        print("  Document AI > Create Processor > Invoice Parser")
        print("Then set: export DOCAI_INVOICE_PROCESSOR_ID=<id>")
        print("  or pass: --invoice-processor-id <id>")
        return None

    doc = invoice_parser.parse_invoice(processor_id, pdf_path)
    if doc is None:
        return None
    entities = invoice_parser.print_invoice_results(doc)
    headline = [e for e in entities if e["is_headline"]]
    print(f"\n[Main] Invoice parsing complete: {len(entities)} entities, "
          f"{len(headline)} headline fields.")
    return entities


def main():
    args = parse_args()

    print("=" * 70)
    print("Project 24: Document AI Form Parser + Invoice Parser")
    print("=" * 70)

    if args.mode in ("form", "both"):
        run_form_demo(args.form_processor_id, args.pdf)

    if args.mode in ("invoice", "both"):
        run_invoice_demo(args.invoice_processor_id, args.pdf)

    print("\n" + "=" * 70)
    print("Done. See above for extracted key-value pairs and invoice entities.")
    print("=" * 70)


if __name__ == "__main__":
    main()
