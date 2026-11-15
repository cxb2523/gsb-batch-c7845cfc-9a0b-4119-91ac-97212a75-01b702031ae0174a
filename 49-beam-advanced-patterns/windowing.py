# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Apache Beam Event-Time Windowing (Local, No Pub/Sub Required)

Demonstrates event-time FixedWindows on a bounded in-memory or file-based
source: order events get event timestamps from their timestamp field and
revenue is summed per 30-day window.

The SAME windowing code you would run on a streaming Pub/Sub pipeline —
without the infrastructure cost of a live stream.

Key concepts:
  - Event time vs processing time: event time is WHEN the event happened;
    processing time is when the pipeline sees it. Windowing on event time gives
    accurate results even when events arrive late or out of order.
  - FixedWindows (30 days): tumbling, non-overlapping, epoch-aligned. True
    calendar months don't tile evenly — for those, window in BigQuery SQL.
  - Watermark: on a bounded source it advances to +inf at end, firing every
    window exactly once. Streaming has a real watermark driven by the source.
  - CombineGlobally.without_defaults(): required inside windows to avoid
    Beam emitting a default value for every window in the global timeline.
"""

import datetime
import json
import os
from pathlib import Path

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.transforms import window

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

DEFAULT_EVENTS_FILE = Path(__file__).resolve().parent / "data" / "events.json"

THIRTY_DAYS_SECONDS = 30 * 24 * 60 * 60


def load_events_from_file(events_file: str = str(DEFAULT_EVENTS_FILE)) -> list[tuple]:
    """Load event records from the JSON file and return as (id, timestamp, region, amount) tuples."""
    with open(events_file) as f:
        records = json.load(f)
    return [
        (r["event_id"], r["event_timestamp"], r["region"], r["total_amount"])
        for r in records
    ]


def to_timestamped(order: tuple) -> window.TimestampedValue:
    """Attach an EVENT-TIME timestamp to each element.

    Without this, every element from beam.Create has timestamp -inf and all
    windowing is meaningless.

    In a Pub/Sub pipeline you would instead publish the event time as a
    message attribute and read it via:
      ReadFromPubSub(timestamp_attribute="event_time")
    Same concept — different plumbing.
    """
    order_id, event_timestamp, region, total = order
    epoch = (
        datetime.datetime.fromisoformat(event_timestamp)
        .replace(tzinfo=datetime.timezone.utc)
        .timestamp()
    )
    return window.TimestampedValue((order_id, region, total), epoch)


class FormatWindowedSum(beam.DoFn):
    """Emit a formatted string for each window with its boundary dates and revenue sum.

    beam.DoFn.WindowParam injects the window an element belongs to — this is
    how you recover window start/end after aggregation.
    """

    def process(self, total, win=beam.DoFn.WindowParam):
        start = win.start.to_utc_datetime().date()
        end = win.end.to_utc_datetime().date()
        yield f"  window [{start} → {end})  revenue = ${total:>10,.2f}"


def run_windowing_pipeline(
    events_file: str = str(DEFAULT_EVENTS_FILE),
    window_size_seconds: int = THIRTY_DAYS_SECONDS,
) -> list[str]:
    """Run event-time windowing pipeline and return formatted result lines."""
    print("=" * 70)
    print("Beam event-time windowing — 30-day fixed windows, revenue per window")
    print(f"Input : {events_file}")
    print("=" * 70)

    events = load_events_from_file(events_file)
    print(f"Loaded {len(events)} events spanning {events[0][1][:7]} → {events[-1][1][:7]}")

    # DirectRunner.
    # For Dataflow streaming:
    #   options = PipelineOptions(
    #       runner="DataflowRunner",
    #       project=PROJECT_ID,
    #       region=REGION,
    #       temp_location="gs://your-gcs-bucket/tmp/",
    #       streaming=True,           # required for unbounded sources
    #   )
    options = PipelineOptions(runner="DirectRunner")

    results: list[str] = []

    with beam.Pipeline(options=options) as pipeline:
        (
            pipeline
            | "CreateEvents" >> beam.Create(events)
            # Attach event-time timestamps from the order_date field.
            | "AssignEventTime" >> beam.Map(to_timestamped)
            # FixedWindows(30d): tumbling, non-overlapping, epoch-aligned.
            # For true calendar months: window in BigQuery SQL instead.
            | "Window30Days" >> beam.WindowInto(window.FixedWindows(window_size_seconds))
            # Extract just the revenue value per event.
            | "ExtractRevenue" >> beam.Map(lambda o: o[2])
            # CombineGlobally inside a window needs without_defaults():
            # otherwise Beam emits a default 0 for every window in the timeline.
            | "SumPerWindow" >> beam.CombineGlobally(sum).without_defaults()
            | "FormatWithBoundaries" >> beam.ParDo(FormatWindowedSum())
            | "Collect" >> beam.Map(results.append)
        )

        print("\nRevenue per 30-day event-time window:")

    for line in sorted(results):
        print(line)

    print(
        "\nDone. Same WindowInto/Combine code runs unchanged on a Pub/Sub stream.\n"
        "Streaming differences:\n"
        "  - Source: ReadFromPubSub(timestamp_attribute='event_time')\n"
        "  - Watermark: real, driven by the source; windows fire as it advances\n"
        "  - Triggers + allowed_lateness: needed for late-arriving events\n"
        "  - Cost: a streaming Dataflow job runs 24/7 — don't leave demo jobs running"
    )

    return results


if __name__ == "__main__":
    run_windowing_pipeline()
