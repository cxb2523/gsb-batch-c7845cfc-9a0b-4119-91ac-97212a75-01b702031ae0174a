# 9. GCS Signed URLs + Lifecycle Rules

> Generate V4 signed URLs for time-limited unauthenticated access to private GCS objects, and configure bucket lifecycle rules for automated storage cost optimization. **GCP Services:** Cloud Storage, IAM

## Architecture

```
Private GCS Object
  gs://<bucket>/data/sales_data.csv
           │
           ▼
   signed_urls.py
   generate_signed_url()
   (V4, impersonated credentials
    or SA key file)
           │
           ▼
   Signed URL (15 min GET)  ──► External client (no GCP auth needed)
   Signed URL (60 min PUT)  ──► Pre-signed upload endpoint

           +

   GCS Bucket
           │
           ▼
   lifecycle.py
   apply_standard_lifecycle_rules()
           │
    ┌──────┴────────┐
    ▼               ▼
Delete tmp/       Transition STANDARD → NEARLINE
after 7 days      after 30 days (~50% cost saving)
(platform-enforced, daily evaluation, zero compute cost)
```

## What this demonstrates

- V4 signed URL generation with automatic credential detection (key file vs impersonation)
- Graceful fallback to impersonated credentials when ADC has no private key
- Signed GET URL (read) and signed PUT URL (pre-signed upload) patterns
- Reading existing bucket lifecycle rules
- Adding `Delete` and `SetStorageClass` lifecycle rules programmatically
- FinOps storage class cost trade-offs (NEARLINE / COLDLINE / ARCHIVE)
- Clearing lifecycle rules cleanly

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GCS_BUCKET=your-gcs-bucket        # must already exist and contain the object
export SIGNER_SA=<sa-email>              # optional; defaults to appspot SA
export BLOB_NAME=data/sales_data.csv     # optional; object to sign
export CLEAR_RULES_AFTER=true            # optional; removes demo rules after run
```

Authenticate:
```bash
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

`deploy.sh` enables APIs and prints exact commands for both the impersonation (Option A) and service account key file (Option B) signing setups.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator: signed URLs + lifecycle rules |
| `signed_urls.py` | `generate_signed_url()` with auto credential detection and impersonation fallback |
| `lifecycle.py` | `apply_standard_lifecycle_rules()`, `clear_lifecycle_rules()`, `print_lifecycle_rules()` |
| `deploy.sh` | API enablement + signed URL auth setup instructions |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
