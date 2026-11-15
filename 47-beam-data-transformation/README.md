# 47. Beam Data Transformation

> Core Apache Beam patterns: WordCount and CSV filter/aggregate pipelines running locally on DirectRunner. **GCP Services:** (optional) Cloud Dataflow, Cloud Storage

## Architecture

```
data/sample_text.txt          data/sales_data.csv
        │                              │
        ▼                              ▼
  wordcount.py                  csv_transform.py
  ReadFromText                  ReadFromText (skip header)
        │                              │
  FlatMap (regex split)          Map (parse_csv_line)
        │                              │
  Map (word, 1)              Filter (category == 'AI')
        │                              │
  CombinePerKey(sum)          Map (region, total_amount)
        │                              │
  Sort + Print               CombinePerKey(sum)
                                       │
                              WriteToText → out/
                                       │
                                    Print

Runner: DirectRunner (local, free)
Dataflow upgrade: change PipelineOptions only — pipeline graph unchanged
```

## What this demonstrates

- Beam core model: PCollection → PTransform DAG → Runner
- WordCount: FlatMap, Map, CombinePerKey — the canonical Beam entry point
- CSV transform: parse, filter early (before shuffle), aggregate with CombinePerKey
- Combiner lifting: pre-aggregation per bundle before shuffle (cheap at TB scale)
- DirectRunner vs DataflowRunner: identical graph, only options differ
- WriteToText sharding behavior: one shard on DirectRunner, N on Dataflow workers

## Prerequisites

No GCP project required for DirectRunner. If using DataflowRunner:

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
gcloud auth application-default login
```

## Setup

```bash
# DirectRunner: no setup needed — skip straight to Run
# DataflowRunner: enable APIs and create staging bucket
chmod +x deploy.sh && ./deploy.sh
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Expected output:
```
[1/2] WordCount pipeline
  dataflow             3
  beam                 3
  pipeline             2
  ...

[2/2] CSV Transform — AI category revenue per region
  US-East,500.0
  APAC,375.0
  EU,390.0
  US-West,1200.0
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator — runs WordCount then CSV transform sequentially |
| `wordcount.py` | WordCount pipeline: reads text file, counts words, prints top-N |
| `csv_transform.py` | CSV pipeline: filter by category, aggregate revenue per region, write output |
| `data/sales_data.csv` | 20-row GCP services sales fixture |
| `data/sample_text.txt` | 100-word GCP/Beam text for WordCount demo |
| `deploy.sh` | DirectRunner needs no setup; script documents DataflowRunner path |
| `requirements.txt` | Python dependencies |

## Dataflow upgrade path

Change only the `PipelineOptions` block in `csv_transform.py`:

```python
options = PipelineOptions(
    runner="DataflowRunner",
    project="your-project-id",
    region="us-central1",
    temp_location="gs://your-gcs-bucket/tmp/",
)
```

And point `csv_path` at a `gs://` URI — local paths don't exist on Dataflow workers.

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
