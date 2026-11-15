import logging
import os
import re
import json
import uuid
import codecs
import requests

from bs4 import BeautifulSoup
from urllib3.util.url import parse_url

from amazon_product_details_scraper.config import (
    DEFAULT_OUTPUT_FILENAME,
    VALID_DOMAIN_NAMES,
)
from amazon_product_details_scraper.core.utils import (
    download_image,
    ensure_folder,
    extract_image_extension,
)


class ScraperError(Exception):
    """Base class for all scraper-related errors."""


class InvalidURLError(ScraperError):
    """Raised when a URL is malformed or does not point to a supported Amazon domain."""


class ProductTitleNotFoundError(ScraperError):
    """Raised when the fetched page does not contain a product title."""


def get_product_detail(url):
    """Extracts product data from an Amazon product page URL.

    This function scrapes the product title, description, and image URLs
    from a given Amazon product page URL.

    Args:
        url (str): The URL of the Amazon product page.

    Returns:
        dict: A dictionary containing the extracted data, including:
            - id (str): The product ASIN (or a generated UUID as fallback).
            - title (str): The product title.
            - description (str): The product description.
            - image_urls (list): A list of image URLs for the product.

    Raises:
        InvalidURLError: If the URL is malformed or its domain is not a
            supported Amazon domain.
        requests.exceptions.RequestException: If the HTTP request fails.
        ProductTitleNotFoundError: If the page contains no product title.
    """
    if not is_valid_url(url):
        raise InvalidURLError("Invalid amazon product URL")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        + "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36"
    }

    response = requests.get(url, headers=headers, timeout=10000)
    response.raise_for_status()  # Raise an exception for non-200 status codes

    soup = BeautifulSoup(response.content, "html.parser")

    asin_element = soup.find("input", id="ASIN")
    item_id = asin_element["value"] if asin_element else str(uuid.uuid4())

    title_element = soup.find("span", id="productTitle")
    title = title_element.text.strip() if title_element else None

    if not title:
        raise ProductTitleNotFoundError(
            f"Product title not found on page for url {url}"
        )

    description_element = soup.find("div", id="feature-bullets")
    description = description_element.text.strip() if description_element else None

    image_block_element = soup.find("div", id="imageBlock_feature_div")

    image_urls = []
    if image_block_element:
        for script_element in image_block_element.find_all("script"):
            script_text = script_element.string if script_element else None
            if script_text and "ImageBlockATF" in script_text:
                image_url_pattern = r'"hiRes":"(.*?)",'
                image_urls.extend(re.findall(image_url_pattern, script_text))

    return {
        "id": item_id,
        "title": title,
        "description": description,
        "image_urls": image_urls,
    }


def write_product_details(product_details, output_dir):
    """Writes product details to a JSON file.

    This function takes a dictionary containing product details and writes
    them to a JSON file in the specified output directory.

    Args:
        product_details (dict): A dictionary containing product data as
            returned by `get_product_detail`.
        output_dir (str): The path to the output directory.
    """

    output_file = os.path.join(output_dir, DEFAULT_OUTPUT_FILENAME)
    with codecs.open(output_file, "w", encoding="utf8") as f:
        json.dump(product_details, f, ensure_ascii=False, indent=2)


def download_product_images(image_urls, output_dir):
    """Downloads product images from the provided URLs, one image at a time.

    Each image is handled independently: a failure downloading one image is
    recorded and the remaining images are still attempted. Images already
    written to disk are kept.

    Args:
        image_urls (list): A list of image URLs for the product.
        output_dir (str): The path to the product output directory. Images
            are saved in an "images" subdirectory inside it.

    Returns:
        dict: A dictionary with the keys:
            - succeeded (int): Number of images downloaded successfully.
            - failed (int): Number of images that failed to download.
            - failures (list[dict]): One entry per failed image, each with
              "url", "file_name" and "reason" keys.
    """

    images_dir = os.path.join(output_dir, "images")
    ensure_folder(images_dir)

    result = {"succeeded": 0, "failed": 0, "failures": []}

    for i, image_url in enumerate(image_urls, start=1):
        file_name = f"image_{i}.{extract_image_extension(image_url)}"
        try:
            download_image(image_url, images_dir, file_name)
            result["succeeded"] += 1
        except Exception as e:  # pylint: disable=broad-exception-caught
            # A single broken image must not abort the remaining downloads.
            logging.error(
                "Failed to download image %s for product directory %s: %s",
                image_url,
                output_dir,
                e,
            )
            result["failed"] += 1
            result["failures"].append(
                {
                    "url": image_url,
                    "file_name": file_name,
                    "reason": str(e) or e.__class__.__name__,
                }
            )

    return result


def is_valid_url(url):
    """Validates the provided URL.

    This function validates the URL and checks if the hostname is a valid
    domain name.

    Args:
        url (str): The URL to validate.

    Returns:
        bool: True if the URL is valid, False otherwise.
    """

    try:
        parsed_url = parse_url(url)
        if not parsed_url.netloc or not parsed_url.scheme:
            return False  # Not a valid URL format
        domain = parsed_url.netloc.lower()

        # Check if the domain ends with any of the valid domains
        return any(domain.endswith(tld.lower()) for tld in VALID_DOMAIN_NAMES)
    except Exception as e:
        logging.error(f"Error validating URL: {e}")
        return False
