# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Apache Beam WordCount Pipeline (DirectRunner)

The canonical "hello Beam" pipeline — reads lines from a text file (or an
inline list), counts words, and prints a sorted leaderboard.

Beam mental model in 3 sentences:
  1. A PCollection is an immutable, distributed dataset. beam.Create gives a
     bounded PCollection; a Pub/Sub source would be unbounded.
  2. A PTransform turns one PCollection into another. The pipeline is a DAG of
     transforms — nothing executes line-by-line.
  3. The runner decides WHERE the DAG runs: DirectRunner = local process (free,
     great for tests); DataflowRunner = managed GCP service (autoscaling, billed).
"""

import os
import re
from pathlib import Path

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

# Default input: sample_text.txt shipped with this project
DEFAULT_TEXT_FILE = Path(__file__).resolve().parent / "data" / "sample_text.txt"


def count_words_from_sentences(sentences: list[str]) -> list[tuple[str, int]]:
    """Run a WordCount pipeline on an in-memory list of sentences.

    Returns a list of (word, count) tuples sorted by count descending.
    """
    results: list[tuple[str, int]] = []

    options = PipelineOptions(runner="DirectRunner")

    with beam.Pipeline(options=options) as pipeline:
        word_counts = (
            pipeline
            | "CreateSentences" >> beam.Create(sentences)
            # FlatMap: 1 sentence → N words (one-to-many).
            | "SplitWords" >> beam.FlatMap(
                lambda line: re.findall(r"[a-z']+", line.lower())
            )
            # Pair each word with 1 → (key, value) shape for combines.
            | "PairWithOne" >> beam.Map(lambda word: (word, 1))
            # CombinePerKey(sum) pre-aggregates per bundle before shuffle —
            # combiner lifting keeps this cheap at TB scale on Dataflow.
            | "CountPerWord" >> beam.CombinePerKey(sum)
        )

        # Capture results into the Python list.
        word_counts | "Collect" >> beam.Map(results.append)

    # Sort outside the pipeline (sorting inside = anti-pattern at scale).
    results.sort(key=lambda kv: -kv[1])
    return results


def count_words_from_file(text_file: str = str(DEFAULT_TEXT_FILE)) -> list[tuple[str, int]]:
    """Run a WordCount pipeline reading from a local text file.

    For DataflowRunner the file must be on GCS (gs://bucket/path).
    """
    results: list[tuple[str, int]] = []

    options = PipelineOptions(runner="DirectRunner")

    # To switch to DataflowRunner:
    #   options = PipelineOptions(
    #       runner="DataflowRunner",
    #       project=PROJECT_ID,
    #       region=REGION,
    #       temp_location="gs://your-gcs-bucket/tmp/",
    #   )
    # And set text_file to a gs:// URI.

    with beam.Pipeline(options=options) as pipeline:
        word_counts = (
            pipeline
            | "ReadFile" >> beam.io.ReadFromText(text_file)
            | "SplitWords" >> beam.FlatMap(
                lambda line: re.findall(r"[a-z']+", line.lower())
            )
            | "PairWithOne" >> beam.Map(lambda word: (word, 1))
            | "CountPerWord" >> beam.CombinePerKey(sum)
        )

        word_counts | "Collect" >> beam.Map(results.append)

    results.sort(key=lambda kv: -kv[1])
    return results


def run_wordcount_demo(
    text_file: str = str(DEFAULT_TEXT_FILE),
    top_n: int = 20,
) -> None:
    """Run WordCount from a text file and print the top-N results."""
    print("=" * 70)
    print("Apache Beam WordCount — DirectRunner (local)")
    print(f"Input : {text_file}")
    print("=" * 70)

    counts = count_words_from_file(text_file)

    print(f"\nTop {top_n} words (word, count):")
    for word, count in counts[:top_n]:
        print(f"  {word:<20} {count}")

    print(f"\nTotal unique words: {len(counts)}")
    print("Done. Same pipeline graph runs unchanged on Dataflow — only options differ.")


if __name__ == "__main__":
    run_wordcount_demo()
