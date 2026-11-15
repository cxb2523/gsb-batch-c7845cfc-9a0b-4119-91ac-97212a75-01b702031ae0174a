# 35. GCP Resource Inventory

> Three-pass GCP inventory: list all projects, enumerate every resource in a project, and run an org-wide filtered asset search — the foundation of any governance, migration, or FinOps baseline. **GCP Services:** Cloud Resource Manager, Cloud Asset Inventory

## Architecture

```
main.py
   │
   ├─[Step 1]─► projects.py: list_all_projects()
   │              Resource Manager v3 search_projects()
   │              → Project ID / Name / State / Parent / Labels
   │
   ├─[Step 2]─► resources.py: list_all_resources_in_project()
   │              CAI search_all_resources (project scope)
   │              → Asset type histogram + sample resources
   │
   └─[Step 3]─► resources.py: list_resources_org_filtered()
                  CAI search_all_resources (org scope or project fallback)
                  Filters: Compute Instances, GCS Buckets, BQ Datasets
                  → Count per type, up to 10 resources per type shown
```

## What this demonstrates

- `search_projects()` scope behavior: org-level vs folder-level vs per-project IAM grants
- Label-based cost allocation gap detection: unlabeled projects can't be attributed in billing export
- Cloud Asset Inventory as the single-call alternative to N per-service list APIs
- Asset type histogram for sprawl detection (service accounts, firewall rules, orphan disks)
- `search_all_resources` with `asset_types` and CAI query filter syntax
- Org-wide vs project-scope fallback pattern for environments without org-level access
- Graceful `PermissionDenied` and `FailedPrecondition` error handling

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
# Optional: set for org-wide search in Step 3
export GCP_ORG_ID=your-org-numeric-id
```

Authenticate:

```bash
gcloud auth application-default login
```

IAM requirements:

| Step | Required role |
|------|--------------|
| Project listing | `roles/browser` at ORG node (or per-project `roles/viewer`) |
| Project resource inventory | `roles/cloudasset.viewer` on the project |
| Org-wide filtered search | `roles/cloudasset.viewer` at the ORG node |

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

The script enables `cloudresourcemanager.googleapis.com` and `cloudasset.googleapis.com`, and prints IAM grant commands.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

With org-wide search (Step 3 full scope):

```bash
export GCP_ORG_ID=$(gcloud organizations list --format='value(name)' | head -1 | cut -d/ -f2)
python main.py
```

Without `GCP_ORG_ID`, Step 3 falls back to project scope automatically.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrates all three inventory passes, prints summary |
| `projects.py` | Project listing via Resource Manager v3 search_projects |
| `resources.py` | CAI full-project inventory + org-wide filtered search |
| `deploy.sh` | Enables APIs, prints IAM requirements |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
