"""Product availability orchestration."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .api_client import AmulAPIClient
from .config import Config
from .models import Product
from .notifier import TelegramNotifier
from .state_manager import RedisStateManager

logger = logging.getLogger(__name__)


class ProductAvailabilityChecker:
    """Coordinate fetching, state tracking, and notifications."""

    def __init__(self) -> None:
        self.api_client = AmulAPIClient()
        self.notifier = TelegramNotifier()
        self.state_manager: Optional[RedisStateManager]
        try:
            self.state_manager = RedisStateManager()
            self.use_state_management = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis not available, falling back to basic notification: %s", exc)
            self.state_manager = None
            self.use_state_management = False

    def _extract_detailed_info(self, detailed_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract and convert detailed product information with safe defaults."""
        if not detailed_info:
            return {
                "inventory_quantity": 0,
                "weight": 0,
                "inventory_low_stock_quantity": 0,
                "total_order_count": 0,
                "compare_price": 0.0,
                "product_type": "",
                "uom": "",
            }

        # Extract numeric fields with safe conversion
        extracted: Dict[str, Any] = {
            "inventory_quantity": int(detailed_info.get("inventory_quantity", 0)),
            "weight": int(detailed_info.get("weight", 0)),
            "inventory_low_stock_quantity": int(detailed_info.get("inventory_low_stock_quantity", 0)),
            "total_order_count": int(detailed_info.get("total_order_count", 0)),
            "compare_price": float(detailed_info.get("compare_price", 0.0)),
        }

        # Extract metafields
        metafields = detailed_info.get("metafields", {})
        extracted["product_type"] = str(metafields.get("product_type", ""))
        extracted["uom"] = str(metafields.get("uom", ""))

        return extracted

    def _create_product_objects(
        self, raw_products: List[Dict[str, Any]], store: str
    ) -> Tuple[List[Product], Set[str]]:
        """Build Product objects from the raw list response.

        Products whose detail page could not be fetched (``None`` in the
        detail map) are still returned: detail-only fields fall back to the
        safe defaults from :meth:`_extract_detailed_info`, while list-level
        fields (``available``, ``price``, ...) are preserved. The second
        return value is the set of aliases with missing details.
        """
        products: List[Product] = []
        aliases = [product.get("alias", "") for product in raw_products if product.get("alias")]
        detailed_info_map = self.api_client.get_product_details_parallel(aliases, max_workers=Config.MAX_WORKERS)
        missing_aliases: Set[str] = set()

        for product in raw_products:
            alias = product.get("alias", "")
            if not alias:
                continue

            detailed_info = detailed_info_map.get(alias)
            if detailed_info is None:
                missing_aliases.add(alias)
            info = self._extract_detailed_info(detailed_info)

            product_obj = Product(
                alias=alias,
                name=product.get("name", "Unknown Product"),
                available=product.get("available", 0) > 0,
                url=f"https://shop.amul.com/product/{alias}",
                store=store,
                price=float(product.get("price", 0)),
                **info,
            )
            products.append(product_obj)

        succeeded = len(products) - len(missing_aliases)
        logger.info(
            "Product details: %s succeeded, %s missing out of %s products",
            succeeded,
            len(missing_aliases),
            len(products),
        )
        if missing_aliases:
            logger.warning(
                "Details missing for %s product(s), keeping list-level fields: %s",
                len(missing_aliases),
                ", ".join(sorted(missing_aliases)),
            )

        return products, missing_aliases

    def _retain_previous_availability(
        self, products: List[Product], missing_aliases: Set[str]
    ) -> None:
        """Carry over the previous round's availability for failed fetches.

        A product whose detail fetch failed must not be treated as confirmed
        out of stock. When state management is available, reuse the previous
        round's "available set": aliases present there stay available,
        others stay unavailable. With no previous state recorded we
        conservatively keep them unavailable so a transient failure cannot
        fabricate a restock notification.
        """
        if not missing_aliases or not self.use_state_management or not self.state_manager:
            return

        # Previous state is keyed per store using exactly the store value
        # carried on the products (which may be an empty string); match the
        # keying used by RedisStateManager instead of assuming a default.
        store = products[0].store if products else Config.DEFAULT_STORE
        previous_available = self.state_manager.get_previous_state(store)
        for product in products:
            if product.alias not in missing_aliases:
                continue
            previous_state = product.alias in previous_available
            if product.available != previous_state:
                logger.info(
                    "Detail fetch failed for %s; retaining previous availability "
                    "(available=%s) instead of list value (available=%s)",
                    product.alias,
                    previous_state,
                    product.available,
                )
            product.available = previous_state

    def check_availability(self) -> Tuple[List[Product], List[Product]]:
        if Config.PINCODE is None:
            raise ValueError("PINCODE must be set in the environment variables")
        store = self.api_client.get_store_from_pincode(Config.PINCODE) if Config.PINCODE else Config.DEFAULT_STORE
        self.api_client.set_store_preferences(store)
        raw_products = self.api_client.get_products()
        if not raw_products:
            raise ValueError("No products found in the API response")
        logger.info("Retrieved %s products from API for store: %s", len(raw_products), store)
        products, missing_aliases = self._create_product_objects(raw_products, store)
        # Failed detail fetches must not be treated as confirmed out of
        # stock: keep the previous round's availability for those aliases so
        # the Redis baseline is not polluted.
        self._retain_previous_availability(products, missing_aliases)
        available = [p for p in products if p.available]
        unavailable = [p for p in products if not p.available]

        # Write timestamp after successful product fetch
        self._write_last_fetch_timestamp()

        return available, unavailable

    def _write_last_fetch_timestamp(self) -> None:
        """Write the current timestamp to indicate successful product fetch."""
        try:
            timestamp_file = '/app/.last_fetch_timestamp'
            current_time = time.time()

            # Ensure the directory exists
            os.makedirs(os.path.dirname(timestamp_file), exist_ok=True)

            # Write the timestamp
            with open(timestamp_file, 'w') as f:
                f.write(str(current_time))

            logger.debug("Updated last fetch timestamp: %s", current_time)
        except Exception as e:
            logger.warning("Failed to write last fetch timestamp: %s", e)

    def _handle_notifications(
        self,
        all_products: List[Product],
        available_products: List[Product],
        should_force_notify: bool,
        dry_run: bool,
    ) -> None:
        if should_force_notify:
            logger.info("Force notify enabled. Sending status for all %s products", len(all_products))
            self.notifier.send_notification(all_products, force=True, log_to_console=dry_run)
            return

        if self.use_state_management and self.state_manager:
            newly_available = self.state_manager.get_newly_available_products(all_products)
            if newly_available:
                logger.info("Found %s newly available products", len(newly_available))
                self.notifier.send_notification(newly_available, log_to_console=dry_run)
            else:
                logger.info("No newly available products to notify about")
            return

        if available_products:
            logger.info(
                "Redis not available - sending basic notification for %s available products",
                len(available_products),
            )
            self.notifier.send_notification(available_products, log_to_console=dry_run)
        else:
            logger.info("No available products to notify about")

    def run(self, force_notify: Optional[bool] = None, dry_run: bool = False) -> None:
        start_time = time.perf_counter()
        available_products, unavailable_products = self.check_availability()
        all_products = available_products + unavailable_products
        should_force_notify = force_notify if force_notify is not None else Config.FORCE_NOTIFY
        self._handle_notifications(all_products, available_products, should_force_notify, dry_run)
        logger.info(
            "Current status - Available: %s, Unavailable: %s",
            len(available_products),
            len(unavailable_products),
        )
        for product in unavailable_products:
            logger.debug("Product unavailable: %s", product.name)
        elapsed = time.perf_counter() - start_time
        logger.info("Run completed in %.2f seconds", elapsed)
