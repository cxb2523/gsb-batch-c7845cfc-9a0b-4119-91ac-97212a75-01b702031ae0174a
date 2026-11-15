# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Apache Beam Branching with Tagged Side Outputs (Dead-Letter Pattern)

A DoFn splits sales rows into 'valid' and 'invalid' branches using TaggedOutput —
the standard dead-letter pattern for production Beam pipelines.

Why this matters in production:
  A single malformed row in a Map() raises an exception. The bundle retries,
  and on Dataflow Streaming the bundle retries FOREVER — one poison message can
  stall a pipeline and burn worker hours. Tagged outputs turn bad data into a
  routable stream:
    - Main output  → BigQuery / destination warehouse
    - Dead-letter  → GCS or BQ dead-letter table for inspection and replay
  Never drop bad rows silently.
"""

import csv
import io
import os
from pathlib import Path

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.pvalue import TaggedOutput

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

DEFAULT_CSV = Path(__file__).resolve().parent / "data" / "sales_data.csv"

CSV_COLUMNS = [
    "order_id", "order_date", "customer_name", "region", "product",
    "category", "quantity", "unit_price", "total_amount",
]


class ValidateRow(beam.DoFn):
    """Route each CSV row to 'valid' or 'invalid' tagged output.

    Validation rules:
      - All fields must parse to their expected types
      - quantity must be > 0
      - total_amount must equal quantity * unit_price within TOLERANCE
        (catches data-entry errors before they corrupt analytics)
    """

    VALID = "valid"
    INVALID = "invalid"
    TOLERANCE = 0.01  # float rounding tolerance for amount check

    def process(self, line):
        # Parse first — any parse failure → dead-letter with reason.
        try:
            row = next(csv.reader(io.StringIO(line)))
            record = dict(zip(CSV_COLUMNS, row))
            record["quantity"] = int(record["quantity"])
            record["unit_price"] = float(record["unit_price"])
            record["total_amount"] = float(record["total_amount"])
        except (ValueError, StopIteration) as exc:
            # Preserve raw line + reason — you need both for replay.
            yield TaggedOutput(self.INVALID, {"raw": line, "reason": f"parse error: {exc}"})
            return

        # Business validation.
        if record["quantity"] <= 0:
            yield TaggedOutput(
                self.INVALID,
                {"raw": line, "reason": f"quantity <= 0 (got {record['quantity']})"},
            )
        elif (
            abs(record["quantity"] * record["unit_price"] - record["total_amount"])
            > self.TOLERANCE
        ):
            expected = record["quantity"] * record["unit_price"]
            yield TaggedOutput(
                self.INVALID,
                {
                    "raw": line,
                    "reason": (
                        f"total != qty*price "
                        f"(expected {expected:.2f}, got {record['total_amount']:.2f})"
                    ),
                },
            )
        else:
            # Untagged yield → main output (mapped to VALID via with_outputs).
            yield record


def run_branching_pipeline(csv_path: str = str(DEFAULT_CSV)) -> dict:
    """Run the branching pipeline and return counts for each branch.

    Returns: {"valid": int, "invalid": int}
    """
    print("=" * 70)
    print("Beam tagged side outputs — valid vs dead-letter rows")
    print(f"Input : {csv_path}")
    print("=" * 70)

    # DirectRunner for local dev.
    # To run on Dataflow:
    #   options = PipelineOptions(
    #       runner="DataflowRunner",
    #       project=PROJECT_ID,
    #       region=REGION,
    #       temp_location="gs://your-gcs-bucket/tmp/",
    #   )
    options = PipelineOptions(runner="DirectRunner")

    valid_count: list[int] = []
    invalid_count: list[int] = []
    dead_letters: list[dict] = []

    with beam.Pipeline(options=options) as pipeline:
        # with_outputs returns a DoOutputsTuple: one PCollection per tag.
        # main=VALID maps untagged yields to the 'valid' branch.
        results = (
            pipeline
            | "ReadCSV" >> beam.io.ReadFromText(csv_path, skip_header_lines=1)
            | "ValidateRows" >> beam.ParDo(ValidateRow()).with_outputs(
                ValidateRow.INVALID, main=ValidateRow.VALID
            )
        )
        valid_rows = results[ValidateRow.VALID]
        invalid_rows = results[ValidateRow.INVALID]

        # Count each branch — Count.Globally() is combiner-lifted (scalable).
        (
            valid_rows
            | "CountValid" >> beam.combiners.Count.Globally()
            | "CaptureValidCount" >> beam.Map(valid_count.append)
        )
        (
            invalid_rows
            | "CountInvalid" >> beam.combiners.Count.Globally()
            | "CaptureInvalidCount" >> beam.Map(invalid_count.append)
        )

        # Show dead-letter rows. In production: WriteToBigQuery / WriteToText
        # into a dead-letter sink, then alert on its rate.
        (
            invalid_rows
            | "CaptureDL" >> beam.Map(dead_letters.append)
        )

        print("\nBranch counts and dead-letter samples:")

    v = valid_count[0] if valid_count else 0
    i = invalid_count[0] if invalid_count else 0

    print(f"  valid rows   : {v}")
    print(f"  invalid rows : {i}")

    if dead_letters:
        print("\nDead-letter details:")
        for dl in dead_letters:
            print(f"  [{dl['reason']}]")
            print(f"    raw: {dl['raw'][:80]}")
    else:
        print("\nNo dead-letter rows — all data passed validation.")

    print(
        "\nProduction: route 'invalid' to a BigQuery dead-letter table, "
        "set an alert on its rate."
    )
    return {"valid": v, "invalid": i}


if __name__ == "__main__":
    run_branching_pipeline()
