"""Business Vault · Bridge — Order (Date-Free)

Pre-joins the order relationship structure:
  lnk_order → hub_order → hub_customer → hub_restaurant

Resolves hash keys to natural business keys for efficient
downstream consumption. Date-free because order relationships
are immutable (event-based domain). PITs handle temporality.

Equivalent to the existing gold _orders_enriched temp view
but vault-native (structural keys only, no satellite attributes).
"""
from pyspark import pipelines as dp

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.bridge_order",
    comment="Bridge — pre-joined order ↔ customer ↔ restaurant relationship keys",
    table_properties={
        "quality": "business_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_order"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_lnk_order IS NOT NULL",
        "fk_order_not_null": "hk_order IS NOT NULL",
        "fk_customer_not_null": "hk_customer IS NOT NULL",
        "fk_restaurant_not_null": "hk_restaurant IS NOT NULL",
    }
)
@dp.expect("bk_order_not_null", "order_id IS NOT NULL")
@dp.expect("bk_customer_not_null", "customer_id IS NOT NULL")
def bridge_order():
    lnk = spark.read.table("restaurant_chain_db.raw_vault.lnk_order")
    h_ord = spark.read.table("restaurant_chain_db.raw_vault.hub_order").select("hk_order", "order_id")
    h_cust = spark.read.table("restaurant_chain_db.raw_vault.hub_customer").select("hk_customer", "customer_id")
    h_rest = spark.read.table("restaurant_chain_db.raw_vault.hub_restaurant").select("hk_restaurant", "restaurant_id")

    return (
        lnk
        .join(h_ord, "hk_order", "inner")
        .join(h_cust, "hk_customer", "inner")
        .join(h_rest, "hk_restaurant", "inner")
        .select(
            "hk_lnk_order",
            "hk_order",
            "hk_customer",
            "hk_restaurant",
            "order_id",
            "customer_id",
            "restaurant_id",
        )
    )
