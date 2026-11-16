# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 25 — Batch Document Processing + Layout-Aware RAG Chunking

Two complementary Document AI workflows:

  1. Batch GCS Processing (batch_processor.py)
     Upload PDFs to GCS → batch_process_documents (async LRO) → read JSON output.
     Use this for large volumes (>15 pages/file, hundreds of documents).

  2. Layout-Aware Chunking (layout_chunker.py)
     Run a LAYOUT_PARSER_PROCESSOR with chunking_config → get structure-aware
     chunks ready for RAG embedding pipelines.
     Use this as the document ingestion step in any enterprise RAG system.

Requires Document AI API enabled. Run deploy.sh first.

Usage:
  python main.py                         # runs both demos
  python main.py --mode batch            # batch GCS processing only
  python main.py --mode layout           # layout chunking only
  python main.py --processor-id <id>     # shared processor ID for both
  python main.py --pdf path/to/file.pdf  # use your own PDF
"""

import argparse
import os
import sys

import batch_processor
import layout_chunker


def parse_args():
    parser = argparse.ArgumentParser(description="Batch Document Processing + Layout Chunking")
    parser.add_argument(
        "--mode",
        choices=["batch", "layout", "both"],
        default="both",
        help="Which demo to run (default: both)",
    )
    parser.add_argument(
        "--processor-id",
        default=os.environ.get("DOCAI_PROCESSOR_ID", ""),
        help="Document AI processor ID (OCR or Layout Parser). "
             "Can also be set via DOCAI_PROCESSOR_ID env var.",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("DOCAI_GCS_BUCKET", "your-gcs-bucket"),
        help="GCS bucket for batch processing input/output. "
             "Can also be set via DOCAI_GCS_BUCKET env var.",
    )
    parser.add_argument(
        "--pdf",
        default=None,
        help="Path to a local PDF file. Defaults to a public sample PDF.",
    )
    return parser.parse_args()


def run_batch_demo(processor_id: str, bucket: str, pdf_path: str | None = None):
    print("\n" + "=" * 70)
    print("DEMO 1: Batch GCS Processing (async LRO)")
    print("=" * 70)

    if not processor_id:
        print("\nERROR: DOCAI_PROCESSOR_ID is required for batch processing.")
        print("Run project 23 (python main.py) to create an OCR processor,")
        print("then: export DOCAI_PROCESSOR_ID=<id>")
        return None

    # Stage the input PDF in GCS
    batch_processor.BUCKET = bucket
    batch_processor.stage_input_pdf(bucket, pdf_path)

    # Run the batch job and poll — a failed/timed-out job is never
    # treated as success.
    success = batch_processor.run_batch_process(processor_id, bucket)
    if not success:
        print("\n[Main] ERROR: batch job failed or timed out; "
              "no results were collected.")
        return None

    # Read results and settle per file
    settlement = batch_processor.read_batch_output(bucket)

    for failure in settlement.failures:
        print(f"[Main] FAILED file: {failure['blob_name']} — {failure['error']}")

    if not settlement.records:
        print("\n[Main] ERROR: batch produced no usable results "
              f"({settlement.failed} file(s) failed). Failing explicitly.")
        return None

    print(f"\n[Main] Batch settlement: {settlement.succeeded} succeeded, "
          f"{settlement.failed} failed.")
    if settlement.failed:
        print("[Main] Partial success — continuing with the successful records; "
              "re-run to retry the failed files.")
    print(f"[Main] Batch processing complete: {settlement.succeeded} output files read.")
    return settlement.records


def run_layout_demo(processor_id: str, pdf_path: str | None = None):
    print("\n" + "=" * 70)
    print("DEMO 2: Layout-Aware Chunking for RAG Ingestion")
    print("=" * 70)

    if not processor_id:
        print("\nERROR: DOCAI_PROCESSOR_ID is required for layout chunking.")
        print("Create a LAYOUT_PARSER_PROCESSOR in the GCP Console:")
        print("  Document AI > Create Processor > Layout Parser")
        print("Then: export DOCAI_PROCESSOR_ID=<id>")
        return None

    chunks = layout_chunker.chunk_pdf(processor_id, pdf_path)
    if chunks is None:
        return None

    layout_chunker.print_chunks(chunks)

    rag_records = layout_chunker.chunks_to_rag_records(chunks)
    total_chars = sum(r["char_count"] for r in rag_records)

    print(f"\n[Main] Layout chunking complete:")
    print(f"  Total chunks   : {len(rag_records)}")
    print(f"  Total chars    : {total_chars:,}")
    print(f"  Avg chunk size : {total_chars // max(len(rag_records), 1):,} chars")
    print("\n[Main] Next step in a RAG pipeline:")
    print("  Embed each record['content'] and upsert into a vector store")
    print("  (Vertex AI Vector Search, BigQuery VECTOR_SEARCH, or FAISS).")
    print("  See project 21 for RAG embedding and retrieval examples.")
    return rag_records


def main():
    args = parse_args()

    print("=" * 70)
    print("Project 25: Batch Document Processing + Layout-Aware RAG Chunking")
    print("=" * 70)

    exit_code = 0

    if args.mode in ("batch", "both"):
        if run_batch_demo(args.processor_id, args.bucket, args.pdf) is None:
            # Batch demo failed explicitly (job failure, timeout, or no
            # usable results) — reflect that in the process exit code.
            exit_code = 1

    if args.mode in ("layout", "both"):
        run_layout_demo(args.processor_id, args.pdf)

    print("\n" + "=" * 70)
    print("Done." if exit_code == 0 else "Done (with failures — see above).")
    print("=" * 70)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
