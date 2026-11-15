"""Business Vault · CDC Intermediate — Reviews Tracked

Pipeline-internal streaming table that uses CDC from snapshot
to detect new/changed review satellite rows. Feeds the AI
enrichment in sat_review_sentiment.

Architecture:
  raw_vault.sat_review (MV snapshot)
      ↓  create_auto_cdc_from_snapshot_flow (SCD1, keys=[hk_review])
  reviews_tracked_dv (ST with CDF — detects new reviews)
      ↓  readStream + readChangeFeed
  sat_review_sentiment (ST — ai_query runs ONCE per review)
"""
from pyspark import pipelines as dp


# --- CDC Target (pipeline-internal, no schema prefix) ---
# Note: Expectations not supported on APPLY CHANGES FROM SNAPSHOT targets.
dp.create_streaming_table(
    name="reviews_tracked_dv",
    comment="CDC tracking table for review satellite — detects new/changed versions for AI enrichment",
)

# --- CDC from Snapshot Flow ---
dp.create_auto_cdc_from_snapshot_flow(
    target="reviews_tracked_dv",
    source="restaurant_chain_db.raw_vault.sat_review",
    keys=["hk_review"],
    stored_as_scd_type=1,
)
