import os
import logging
import argparse

from amazon_product_details_scraper.config import DEFAULT_LOG_LEVEL
from amazon_product_details_scraper.core.utils import read_file
from amazon_product_details_scraper.core.batch import process_url_batch


def main():
    """Main function for the script.

    This function parses command-line arguments, reads product URLs,
    processes each URL independently through the batch pipeline (which
    writes per-product details, optional images and a batch summary), and
    logs the resulting counts.
    """

    logging.basicConfig(level=DEFAULT_LOG_LEVEL)

    parser = argparse.ArgumentParser(description="Amazon Product Details Scrapper")

    # A mutually exclusive group for URL input (either single URL or file)
    required_group = parser.add_mutually_exclusive_group(required=True)
    required_group.add_argument(
        "-u",
        "--url",
        type=str,
        help="URL of the Amazon product page you want to scrape.",
        metavar="<url>",
    )
    required_group.add_argument(
        "-ul",
        "--url-list",
        type=str,
        help="Path to a file containing a list of Amazon product URLs (one per line).",
        metavar="<file-path>",
    )

    # Optional arguments
    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default="output",
        help="Output directory to store scraped data (default: output).",
        metavar="<file-path>",
    )
    parser.add_argument(
        "-d",
        "--download-image",
        action="store_true",
        help="Download product images along with details (default: False).",
    )

    args = parser.parse_args()

    input_url = args.url
    url_list_path = args.url_list
    output_dir = os.path.normpath(args.output_dir)
    download_image = args.download_image

    if input_url:
        # Trim surrounding whitespace and ignore an effectively empty URL.
        url_list = [input_url.strip()] if input_url.strip() else []
    else:
        try:
            url_list_path = os.path.normpath(url_list_path)
            url_list = read_file(url_list_path)
        except FileNotFoundError:
            logging.error(f"Error: URL list file not found: {url_list_path}")
            exit(1)

    process_url_batch(url_list, output_dir, download_image=download_image)


if __name__ == "__main__":
    main()
