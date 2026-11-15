# 37. Compute + Data Estate Inventory

> Unified inventory across Compute Engine (VMs + disks) and the data estate (BigQuery + GCS), with FinOps cost flags baked in. **GCP Services:** Compute Engine, BigQuery, Cloud Storage

## Architecture

```
main.py
  |
  |-- compute_inventory.py --> Compute Engine API (aggregated_list)
  |     All zones in one call   -> Flag TERMINATED VMs with disks
  |                             -> Flag unattached (orphan) disks
  |
  |-- data_estate.py -------> BigQuery API
  |     List datasets/tables    -> Logical size per table
  |     + GCS API               -> Flag tables without partitioning
  |       List buckets          -> Flag buckets with no lifecycle rules
  |
  v
  Consolidated Inventory Report (stdout)
```

## What this demonstrates

- Using `aggregated_list` to sweep all Compute zones in a single API call
- Identifying TERMINATED VMs with attached disks (disk billing continues when VM is stopped)
- Detecting orphaned (unattached) persistent disks
- Fetching BigQuery dataset and table metadata including logical byte sizes
- Listing GCS buckets and checking for lifecycle rule coverage
- Surfacing FinOps cost flags across compute and storage in a single report

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1

# Authenticate
gcloud auth application-default login
```

The running principal needs:

| Permission scope | Minimum role |
|---|---|
| List Compute VMs + disks | `roles/compute.viewer` |
| List BQ datasets + tables | `roles/bigquery.metadataViewer` |
| List GCS buckets | `roles/storage.objectViewer` |

`roles/viewer` on the project covers all three if you prefer a single binding.

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

## Files

| File | Purpose |
|---|---|
| `main.py` | Orchestrates compute + data estate phases; prints consolidated report |
| `compute_inventory.py` | Inventories VMs and disks via aggregated_list; flags cost waste |
| `data_estate.py` | Inventories BigQuery and GCS; flags partitioning and lifecycle gaps |
| `deploy.sh` | Enables required APIs and prints IAM grant commands |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
