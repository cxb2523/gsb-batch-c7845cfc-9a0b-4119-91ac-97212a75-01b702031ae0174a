# 38. Billing + Cost Recommendations

> Lists billing accounts and project billing linkage, queries the BigQuery billing export for cost by service (last 30 days), and sweeps the Recommender API for actionable cost-saving opportunities. **GCP Services:** Cloud Billing API, BigQuery, Recommender API

## Architecture

```
main.py
  |
  |-- billing.py -----------> Cloud Billing API
  |     List billing accounts   -> Project billing linkage
  |     + BigQuery (optional)   -> Cost by service last 30 days (gross + net with credits)
  |
  |-- recommendations.py ---> Recommender API (5 cost recommenders)
        Idle VMs                -> Rightsizing recommendations
        Idle disks/images       -> CUD purchase recommendations
        Sweeps 10 locations
              |
              v
        FinOps Summary (stdout)
```

## What this demonstrates

- Listing billing accounts and reading project billing linkage via Cloud Billing API
- Running the canonical cost-by-service query (gross vs net cost including credits) against a BigQuery billing export
- Sweeping 5 cost-focused Recommender types across 10 GCP locations in a single script
- Reading `primary_impact.cost_projection` to extract estimated savings amounts
- Handling the billing IAM hierarchy (billing-account-level vs project-level permissions)
- Providing detailed instructions for setting up billing export (the #1 FinOps prerequisite)

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1

# Optional: set this to enable cost-by-service query from BigQuery export
export BILLING_EXPORT_TABLE=your-project-id.your_dataset.gcp_billing_export_resource_v1_XXXXXX

# Authenticate
gcloud auth application-default login
```

The running principal needs:

| Permission scope | Minimum role |
|---|---|
| List billing accounts | `roles/billing.viewer` on the **billing account** |
| Read cost recommendations | `roles/recommender.viewer` on the project |
| Query billing export | `roles/bigquery.dataViewer` on the export dataset |

Note: Billing IAM is a separate hierarchy from project IAM — billing viewer must be granted on the billing account directly, not the project.

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

To include the BigQuery cost breakdown:

```bash
export BILLING_EXPORT_TABLE=your-project-id.your_dataset.gcp_billing_export_resource_v1_XXXXXX
python main.py
```

## Files

| File | Purpose |
|---|---|
| `main.py` | Orchestrates billing + export + recommender phases; prints FinOps summary |
| `billing.py` | Lists billing accounts, reads project linkage, runs export cost query |
| `recommendations.py` | Sweeps 5 Recommender types across 10 locations; extracts savings estimates |
| `deploy.sh` | Enables APIs, prints IAM grant commands, explains billing export setup |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
