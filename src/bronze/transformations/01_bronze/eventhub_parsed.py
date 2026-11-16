"""Bronze · Parsed EventHub Orders and Rejections (Temporary Views)

Pipeline-private views that parse EventHub JSON without persisting to storage:

* ``eventhub_parsed``          — successfully parsed orders, feeding the
  ``01_bronze.orders`` live flow. Same column shape as before.
* ``eventhub_rejected``        — every message that could not be turned into an
  order, carrying the raw payload and the rejection reason. Feeds the
  persisted ``01_bronze.order_rejections`` table.

The split is explicit (see ``_shared/order_ingest.py``) rather than relying on
a soft expectation: malformed JSON and missing/blank ``order_id`` records are
routed to the rejection stream and can never silently enter ``orders``.
"""
import os
import sys

from pyspark import pipelines as dp

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
from _shared.order_ingest import split_parsed_orders  # noqa: E402


@dp.temporary_view()
def eventhub_parsed():
    """Parsed, order_id-bearing messages. Downstream shape is unchanged."""
    good, _rejected = split_parsed_orders(
        spark.readStream.table("restaurant_chain_db.00_landing.eventhub_raw")
    )
    return good


@dp.temporary_view()
def eventhub_rejected():
    """Rejected messages with raw value, landing timestamps and reason."""
    _good, rejected = split_parsed_orders(
        spark.readStream.table("restaurant_chain_db.00_landing.eventhub_raw")
    )
    return rejected
