# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Billing module: lists billing accounts and the project's billing linkage,
then runs the canonical cost-by-service-last-30-days query against the
BigQuery billing export (if BILLING_EXPORT_TABLE is set).
Billing export is the #1 FinOps prerequisite — no export, no real cost analysis.
"""

import os

from google.api_core import exceptions
from google.cloud import billing_v1

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
BILLING_EXPORT_TABLE = os.environ.get("BILLING_EXPORT_TABLE", "")


def list_billing_accounts(project_id: str = PROJECT_ID) -> dict:
    """
    Lists all visible billing accounts and the billing linkage for project_id.

    Returns a dict with:
      - 'accounts': list of billing account names
      - 'billing_enabled': bool for the project
      - 'billing_account': str billing account linked to the project
    """
    client = billing_v1.CloudBillingClient()
    result = {"accounts": [], "billing_enabled": False, "billing_account": ""}

    print("\nBILLING ACCOUNTS")
    print("-" * 75)
    try:
        accounts = list(client.list_billing_accounts())
        for acct in accounts:
            status = "OPEN" if acct.open_ else "CLOSED"
            print(f"  {acct.name}  '{acct.display_name}'  [{status}]")
            result["accounts"].append(acct.name)
        if not accounts:
            print("  (none visible — billing IAM is separate from project IAM)")
    except exceptions.PermissionDenied:
        print("  Permission denied: needs roles/billing.viewer ON THE BILLING")
        print("  ACCOUNT (not the project) — billing IAM is its own hierarchy.")

    print("\nPROJECT BILLING LINKAGE")
    print("-" * 75)
    try:
        info = client.get_project_billing_info(name=f"projects/{project_id}")
        if info.billing_enabled:
            print(f"  {project_id} -> {info.billing_account_name} (enabled)")
            result["billing_enabled"] = True
            result["billing_account"] = info.billing_account_name
        else:
            print(f"  {project_id}: billing DISABLED — most APIs will refuse to work.")
    except exceptions.PermissionDenied:
        print("  Permission denied: needs resourcemanager.projects.get / billing viewer.")

    return result


def query_billing_export(export_table: str = BILLING_EXPORT_TABLE, project_id: str = PROJECT_ID) -> None:
    """
    Runs the canonical cost-by-service-last-30-days query against the
    BigQuery billing export table.

    The canonical FinOps starter query: cost + credits by service, last 30d.
    Credits matter — SUDs/CUDs/promos can be 20-40% of gross; reporting cost
    without credits overstates spend and breaks chargeback.
    """
    from google.cloud import bigquery

    print("\nCOST BY SERVICE — LAST 30 DAYS (billing export)")
    print("-" * 75)
    sql = f"""
        SELECT
          service.description AS service,
          ROUND(SUM(cost), 2) AS gross_cost,
          ROUND(SUM(cost) + SUM(IFNULL((
            SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS net_cost,
          currency
        FROM `{export_table}`
        WHERE usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
        GROUP BY service, currency
        ORDER BY net_cost DESC
        LIMIT 15
    """
    client = bigquery.Client(project=project_id)
    try:
        rows = list(client.query(sql).result())
    except (exceptions.PermissionDenied, exceptions.NotFound, exceptions.BadRequest) as e:
        print(f"  Query failed: {e.message}")
        print("  Check BILLING_EXPORT_TABLE format: project.dataset.table, and that")
        print("  you have roles/bigquery.dataViewer on the export dataset.")
        return
    print(f"  {'SERVICE':<40} {'GROSS':>12} {'NET':>12}")
    for r in rows:
        print(f"  {r.service:<40} {r.gross_cost:>12,.2f} {r.net_cost:>12,.2f} {r.currency}")
