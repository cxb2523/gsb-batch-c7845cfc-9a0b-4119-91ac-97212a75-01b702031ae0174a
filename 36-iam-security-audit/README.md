# 36. IAM Security Audit

> Three-phase GCP security audit: API sprawl, IAM policy hygiene, and service account key age — consolidated into a single risk report. **GCP Services:** Service Usage API, Cloud Resource Manager, IAM API

## Architecture

```
main.py
  |
  |-- api_audit.py -------> Service Usage API
  |     List enabled APIs     -> Flag >40 APIs (sprawl warning)
  |
  |-- iam_audit.py -------> Cloud Resource Manager
  |     Get IAM policy        -> Flag primitive roles / public principals
  |
  |-- sa_key_audit.py ----> IAM API
        List SA + user keys   -> Flag keys older than 90 days
              |
              v
        Consolidated Risk Summary (printed to stdout)
```

## What this demonstrates

- Querying the Service Usage API with pagination to enumerate all enabled APIs
- Fetching and parsing project-level IAM policy bindings programmatically
- Listing service accounts and filtering for user-managed (exported) keys
- Computing key age vs. a configurable compliance threshold (default: 90 days, CIS GCP 1.7)
- Aggregating multi-source security signals into a single risk summary
- Handling `PermissionDenied` gracefully so partial results still print

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
| List enabled APIs | `roles/serviceusage.serviceUsageViewer` |
| Read IAM policy | `roles/iam.securityReviewer` |
| List SA + keys | `roles/iam.securityReviewer` |

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
| `main.py` | Orchestrates all three audit phases; prints consolidated risk summary |
| `api_audit.py` | Lists enabled APIs via Service Usage; warns on API sprawl |
| `iam_audit.py` | Fetches IAM policy; flags primitive roles and public principals |
| `sa_key_audit.py` | Lists service accounts and user-managed key ages; flags stale keys |
| `deploy.sh` | Enables required APIs and prints IAM grant commands |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
