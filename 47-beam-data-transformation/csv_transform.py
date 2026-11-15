# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Apache Beam CSV Transform Pipeline — Filter + Aggregate (DirectRunner)

Reads sales_data.csv, filters to a given category (default: 'AI'), and
computes total revenue per region using CombinePerKey(sum).

Results are written to an out/ directory and printed to stdout.

This is the batch-ETL skeleton you reuse everywhere:
  - Fits in RAM, one-off analysis       → pandas (less ceremony)
  - Data already in BigQuery            → SQL (GROUP BY beats any pipeline)
  - Multi-GB+ files, reusable ETL,
    same code for batch + streaming     → Beam earns its place
"""

import csv
import io
import os
from pathlib import Path

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

DEFAULT_CSV = Path(__file__).resolve().parent / "data" / "sales_data.csv"
OUT_DIR = Path(__file__).resolve().parent / "out"

CSV_COLUMNS = [
    "order_id", "order_date", "customer_name", "region", "product",
    "category", "quantity", "unit_price", "total_amount",
]


def parse_csv_line(line: str) -> dict:
    """Parse one CSV line into a typed dict.

    Uses the csv module — NOT line.split(",") — because fields like
    customer_name can be quoted and contain commas ("Acme, Inc.").
    """
    row = next(csv.reader(io.StringIO(line)))
    record = dict(zip(CSV_COLUMNS, row))
    record["quantity"] = int(record["quantity"])
    record["unit_price"] = float(record["unit_price"])
    record["total_amount"] = float(record["total_amount"])
    return record


def build_pipeline(
    pipeline: beam.Pipeline,
    csv_path: str,
    category_filter: str,
    out_prefix: str,
) -> beam.PCollection:
    """Construct the filter + aggregate pipeline graph.

    Filter BEFORE the shuffle/combine — cheaper than filtering after.
    CombinePerKey(sum) pre-aggregates per bundle (combiner lifting) before
    shuffling keys — the Beam analog of GROUP BY with a pre-aggregate.
    """
    revenue_per_region = (
        pipeline
        # skip_header_lines=1: drop the column-header row at the source.
        | "ReadCSV" >> beam.io.ReadFromText(csv_path, skip_header_lines=1)
        | "ParseRows" >> beam.Map(parse_csv_line)
        # Filter early — before any shuffle.
        | "FilterCategory" >> beam.Filter(
            lambda r, cat=category_filter: r["category"] == cat
        )
        | "KeyByRegion" >> beam.Map(lambda r: (r["region"], r["total_amount"]))
        # MapTuple unpacks (key, value) into named args — cleaner than kv[0]/kv[1].
        | "SumRevenuePerRegion" >> beam.CombinePerKey(sum)
        | "FormatLine" >> beam.MapTuple(
            lambda region, revenue: f"{region},{revenue:.2f}"
        )
    )

    # WriteToText shards output (-SS-of-NN). DirectRunner usually produces one
    # shard; Dataflow produces one per worker bundle unless forced (forcing
    # 1 shard kills write parallelism at scale — avoid it).
    revenue_per_region | "WriteLocalOut" >> beam.io.WriteToText(
        out_prefix, file_name_suffix=".txt", shard_name_template="-SS-of-NN"
    )

    return revenue_per_region


def run_csv_transform(
    csv_path: str = str(DEFAULT_CSV),
    category_filter: str = "AI",
    out_dir: Path = OUT_DIR,
) -> list[str]:
    """Run the CSV transform pipeline and return result lines.

    To switch to DataflowRunner:
      - Point csv_path at a gs:// URI
      - Set options runner="DataflowRunner", project, region, temp_location
      - Local file paths do not exist on Dataflow workers
    """
    print("=" * 70)
    print(f"Beam CSV transform — '{category_filter}' category revenue per region")
    print(f"Input : {csv_path}")
    print("=" * 70)

    out_dir.mkdir(exist_ok=True)
    out_prefix = str(out_dir / f"{category_filter.lower()}_revenue_by_region")

    # DirectRunner (local, free).
    # To run on Dataflow (see 06_beam_submit_to_dataflow.py for full example):
    #   options = PipelineOptions(
    #       runner="DataflowRunner",
    #       project=PROJECT_ID,
    #       region=REGION,
    #       temp_location="gs://your-gcs-bucket/tmp/",
    #   )
    options = PipelineOptions(runner="DirectRunner")

    results: list[str] = []

    with beam.Pipeline(options=options) as pipeline:
        revenue = build_pipeline(pipeline, csv_path, category_filter, out_prefix)
        revenue | "Collect" >> beam.Map(results.append)
        revenue | "PrintResult" >> beam.Map(lambda line: print(f"  {line}"))

        print(f"\n{category_filter}-category revenue per region (region,revenue):")

    print(f"\nAlso written to: {out_prefix}-*.txt")
    return results


if __name__ == "__main__":
    run_csv_transform()
