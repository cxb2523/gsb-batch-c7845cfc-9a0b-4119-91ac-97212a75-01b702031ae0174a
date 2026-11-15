# 8. GCS File Management

> Full lifecycle demo of Cloud Storage object operations: create bucket, upload, list, metadata, copy, rename, download, and delete. **GCP Services:** Cloud Storage

## Architecture

```
data/sales_data.csv (local)
        │
        ▼
  upload_from_filename ──────────────────────┐
                                             │
  upload_from_string (inline cost report) ───┤
                                             ▼
                              ┌──────────────────────────┐
                              │  GCS Bucket (demo-XXXX)  │
                              │                          │
                              │  demo/                   │
                              │    sales_data.csv        │
                              │    cost_report.csv       │
                              │  archive/                │
                              │    sales_data_copy.csv   │
                              │    cost_report_final.csv │
                              └──────────────────────────┘
                                             │
            ┌────────────┬──────────────┬────┴──────┐
            ▼            ▼              ▼            ▼
     list_blobs   get/set metadata  copy_blob  download
                                   rename_blob  delete
                                              │
                                              ▼
                                    Bucket deleted (cleanup)
```

## What this demonstrates

- Creating a GCS bucket with unique name, STANDARD class, and uniform bucket-level access
- `upload_from_filename` for local files; `upload_from_string` for in-memory content
- Flat listing with prefix; folder-style listing with `delimiter="/"`
- Reading and patching standard (`content_type`, `cache_control`) and custom metadata
- `generation` vs `metageneration` counters for optimistic concurrency
- Copying a blob within the same bucket
- Renaming a blob (copy + delete — GCS has no native rename)
- Downloading to a local file; downloading as text
- Single-blob delete and bulk prefix delete (with safety listing first)
- Full cleanup: bucket deleted at the end

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
```

Authenticate:
```bash
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

The script creates a temporary bucket, runs the full lifecycle, and deletes the bucket on exit (even on failure, via `try/finally`).

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator: 12-step full lifecycle demo |
| `bucket_ops.py` | `create_demo_bucket()` and `delete_bucket()` |
| `object_ops.py` | Upload, download, list, copy, rename, delete, metadata |
| `data/sales_data.csv` | Local file used as upload demo |
| `deploy.sh` | API enablement |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
