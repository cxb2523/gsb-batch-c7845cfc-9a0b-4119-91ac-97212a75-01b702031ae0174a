"""Batch processing for multiple Amazon product URLs.

Each URL in a batch is settled independently: a failure of one URL (invalid
domain, request error, missing product title, ...) is recorded in the batch
summary without affecting the remaining URLs. Image downloads are also
tolerated per image, and product directories are never overwritten when the
same product id appears more than once in a batch.
"""

import codecs
import json
import logging
import os

from amazon_product_details_scraper.config import BATCH_SUMMARY_FILENAME
from amazon_product_details_scraper.core.scraper import (
    ScraperError,
    download_product_images,
    get_product_detail,
    write_product_details,
)
from amazon_product_details_scraper.core.utils import ensure_folder


def _allocate_output_dir(output_dir, product_id, product_dir_counts):
    """Picks an output directory for a product, never reusing a suffixed one.

    The first occurrence of a product id gets ``<output_dir>/<product_id>``.
    Every further occurrence gets its own numbered directory
    ``<output_dir>/<product_id>_2``, ``<product_id>_3`` and so on, so earlier
    output can never be overwritten.

    Args:
        output_dir (str): The base output directory of the batch.
        product_id (str): The product id (ASIN).
        product_dir_counts (dict): Running per-id occurrence counter.

    Returns:
        str: The absolute-ish path allocated for this occurrence.
    """
    product_dir_counts[product_id] = product_dir_counts.get(product_id, 0) + 1
    occurrence = product_dir_counts[product_id]

    if occurrence == 1:
        product_dir = os.path.join(output_dir, product_id)
    else:
        product_dir = os.path.join(output_dir, f"{product_id}_{occurrence}")

    # Do not wipe existing data: only create if missing.
    ensure_folder(product_dir)
    return product_dir


def _process_single_url(url, output_dir, download_image, product_dir_counts):
    """Processes one URL and returns a summary entry for it.

    Any exception raised while handling the URL is caught and turned into a
    failure entry, so the caller can keep processing the remaining URLs.

    Args:
        url (str): The product URL to process.
        output_dir (str): The base output directory of the batch.
        download_image (bool): Whether product images should be downloaded.
        product_dir_counts (dict): Running per-id occurrence counter shared
            across the batch.

    Returns:
        dict: A summary entry describing the outcome of this URL.
    """
    entry = {
        "url": url,
        "status": "failed",
        "product_id": None,
        "output_dir": None,
        "error": None,
        "images": {"succeeded": 0, "failed": 0, "failures": []},
    }

    try:
        product_details = get_product_detail(url)
    except ScraperError as e:
        entry["error"] = str(e)
        logging.error("Failed to fetch product details for %s: %s", url, e)
        return entry
    except Exception as e:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # Settle every URL independently: any unexpected error is recorded
        # as this URL's failure and the batch continues with the next one.
        entry["error"] = str(e) or e.__class__.__name__
        logging.error("Failed to fetch product details for %s: %s", url, e)
        return entry

    product_id = product_details["id"]
    entry["product_id"] = product_id

    product_output_dir = _allocate_output_dir(
        output_dir, product_id, product_dir_counts
    )
    entry["output_dir"] = product_output_dir

    if product_dir_counts[product_id] > 1:
        logging.warning(
            "Product id %s appears more than once in this batch; "
            "url %s is written to separate directory %s",
            product_id,
            url,
            product_output_dir,
        )

    try:
        # product-info.json is written before any image work, so a later
        # image failure keeps the already written details.
        write_product_details(product_details, product_output_dir)
    except Exception as e:  # pylint: disable=broad-exception-caught
        entry["error"] = str(e) or e.__class__.__name__
        logging.error(
            "Failed to write product details for %s to %s: %s",
            url,
            product_output_dir,
            e,
        )
        return entry

    if download_image:
        entry["images"] = download_product_images(
            product_details.get("image_urls", []), product_output_dir
        )

    entry["status"] = "success"
    logging.info("Fetched data for url %s", url)
    return entry


def _build_duplicate_groups(entries):
    """Groups the successful entries that share the same product id.

    Args:
        entries (list[dict]): The per-URL summary entries.

    Returns:
        dict: Mapping of product id to the list of URLs pointing at it
        (only ids reached by more than one URL are included).
    """
    urls_by_product_id = {}
    for entry in entries:
        if entry["status"] == "success" and entry["product_id"]:
            urls_by_product_id.setdefault(entry["product_id"], []).append(
                entry["url"]
            )

    return {
        product_id: urls
        for product_id, urls in urls_by_product_id.items()
        if len(urls) > 1
    }


def write_batch_summary(summary, output_dir):
    """Writes the batch summary JSON into the output directory.

    Args:
        summary (dict): The batch summary structure.
        output_dir (str): The base output directory of the batch.
    """
    ensure_folder(output_dir)
    summary_path = os.path.join(output_dir, BATCH_SUMMARY_FILENAME)
    with codecs.open(summary_path, "w", encoding="utf8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary_path


def process_url_batch(url_list, output_dir, download_image=False):
    """Processes a batch of product URLs and writes a batch summary.

    Every URL is settled independently. A failure of one URL never aborts the
    batch and never removes any already written ``product-info.json`` or
    downloaded image. When the same product id occurs several times, each
    occurrence gets its own numbered output directory.

    Args:
        url_list (list[str]): Product URLs to process. Blank lines and
            surrounding whitespace should already be removed by the caller.
        output_dir (str): Directory in which per-product directories and the
            batch summary file are created.
        download_image (bool): Whether product images should be downloaded.

    Returns:
        dict: The batch summary, also written to
        ``<output_dir>/batch-summary.json``. It contains overall counts, the
        per-URL entries (each with url, status, product id, output directory
        or failure reason, and image success/failure counts) and the groups
        of URLs sharing a product id.
    """
    ensure_folder(output_dir)

    product_dir_counts = {}
    entries = [
        _process_single_url(url, output_dir, download_image, product_dir_counts)
        for url in url_list
    ]

    success_count = sum(1 for entry in entries if entry["status"] == "success")
    failed_count = len(entries) - success_count
    total_images_succeeded = sum(entry["images"]["succeeded"] for entry in entries)
    total_images_failed = sum(entry["images"]["failed"] for entry in entries)

    summary = {
        "total_urls": len(entries),
        "succeeded": success_count,
        "failed": failed_count,
        "images_succeeded": total_images_succeeded,
        "images_failed": total_images_failed,
        "duplicate_product_ids": _build_duplicate_groups(entries),
        "entries": entries,
    }

    write_batch_summary(summary, output_dir)

    logging.info(
        "Batch complete: %s URL(s) processed, %s succeeded, %s failed; "
        "images: %s succeeded, %s failed. Summary written to %s",
        summary["total_urls"],
        success_count,
        failed_count,
        total_images_succeeded,
        total_images_failed,
        os.path.join(output_dir, BATCH_SUMMARY_FILENAME),
    )

    return summary
