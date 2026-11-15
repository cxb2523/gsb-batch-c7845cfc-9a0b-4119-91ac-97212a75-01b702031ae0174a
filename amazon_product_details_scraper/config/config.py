import os
import logging


DEFAULT_LOG_LEVEL = logging.INFO

DEFAULT_OUTPUT_FILENAME = "product-info.json"
DEFAULT_OUTPUT_FOLDER = os.path.expanduser("../output")

# File name of the per-batch summary written to the output directory
BATCH_SUMMARY_FILENAME = "batch-summary.json"

VALID_DOMAIN_NAMES = [
    "amazon.com",
    "amazon.ca",
    "amazon.co.uk",
    "amazon.de",
    "amazon.fr",
    "amazon.in",
    "amazon.it",
    "amazon.co.jp",
    "amazon.cn",
    "amazon.com.mx",
    "amazon.com.au",
    "amazon.nl",
    "amazon.pl",
    "amazon.sg",
    "amazon.sa",
    "amazon.es",
    "amazon.se",
    "amazon.ae",
    "amazon.br",
    "amazon.com.tr",
    "amzn.to",
]
