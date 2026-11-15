Document AI PDF Requirements
============================

Document AI requires actual PDF files — it cannot process plain text files.

HOW TO ADD YOUR OWN PDF
------------------------
1. Place a PDF file in this directory (or anywhere on your filesystem).
2. Pass the path when running the pipeline:

   python main.py --pdf data/your-document.pdf

SAMPLE PDF (default)
---------------------
If you run `python main.py` without the --pdf argument, the pipeline
automatically downloads this public sample PDF from Google Cloud:

   https://storage.googleapis.com/cloud-samples-data/documentai/loan_form.pdf

This is a multi-page loan application form suitable for demonstrating OCR
text extraction and per-page statistics.

SUPPORTED FORMATS
-----------------
- PDF (application/pdf)         — most common; supports scanned + digital PDFs
- TIFF (image/tiff)             — for high-resolution scanned documents
- GIF, JPEG, PNG, BMP, WEBP     — for single-page image documents

SIZE LIMITS (synchronous API)
------------------------------
- Up to 15 pages per request
- Up to 20 MB file size

For documents larger than 15 pages, use the batch processing pipeline
in project 25-batch-document-processing, which reads from GCS and
writes JSON output back to GCS with no page limit.

PROCESSOR ID
------------
The first run creates an OCR_PROCESSOR in your GCP project and prints
the processor ID. Subsequent runs reuse the same processor (idempotent).
You can also set DOCAI_PROCESSOR_ID env var to skip the lookup step:

   export DOCAI_PROCESSOR_ID=your-processor-id
   python main.py
