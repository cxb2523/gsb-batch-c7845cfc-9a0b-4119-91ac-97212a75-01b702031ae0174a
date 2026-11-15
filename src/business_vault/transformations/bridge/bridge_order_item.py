"""Business Vault · Bridge — Order Item (Date-Free)

Pre-joins the full order-item relationship chain:
  lnk_order_item → lnk_order → hub_order → hub_customer
                             → hub_restaurant → hub_menu_item

Resolves all hash keys to natural business keys. Enables the
marts fact_order_item to join a single bridge instead of 6 tables.

Equivalent to the existing gold _order_items_enriched temp view
but vault-native.
"""
from pyspark import pipelines as dp

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.bridge_order_item",
    comment="Bridge — pre-joined order-item full relationship chain",
    table_properties={
        "quality": "business_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_order"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_lnk_order_item IS NOT NULL",
        "fk_order_not_null": "hk_order IS NOT NULL",
        "fk_menu_item_not_null": "hk_menu_item IS NOT NULL",
    }
)
@dp.expect("bk_order_not_null", "order_id IS NOT NULL")
@dp.expect("bk_item_not_null", "item_id IS NOT NULL")
def bridge_order_item():
    lnk_oi = spark.read.table("restaurant_chain_db.raw_vault.lnk_order_item")
    lnk_o = spark.read.table("restaurant_chain_db.raw_vault.lnk_order").select(
        "hk_order", "hk_customer", "hk_restaurant"
    )
    h_ord = spark.read.table("restaurant_chain_db.raw_vault.hub_order").select("hk_order", "order_id")
    h_cust = spark.read.table("restaurant_chain_db.raw_vault.hub_customer").select("hk_customer", "customer_id")
    h_rest = spark.read.table("restaurant_chain_db.raw_vault.hub_restaurant").select("hk_restaurant", "restaurant_id")
    h_menu = spark.read.table("restaurant_chain_db.raw_vault.hub_menu_item").select("hk_menu_item", "restaurant_id", "item_id")

    return (
        lnk_oi
        .join(lnk_o, "hk_order", "inner")
        .join(h_ord, "hk_order", "inner")
        .join(h_cust, "hk_customer", "inner")
        .join(h_rest, "hk_restaurant", "inner")
        .join(h_menu, "hk_menu_item", "inner")
        # Resolve restaurant_id ambiguity: hub_restaurant vs hub_menu_item both have it.
        # Use hub_restaurant's version (canonical restaurant identity).
        .select(
            "hk_lnk_order_item",
            "hk_order",
            "hk_menu_item",
            "hk_customer",
            "hk_restaurant",
            "order_id",
            "customer_id",
            h_rest["restaurant_id"],
            h_menu["item_id"],
        )
    )
