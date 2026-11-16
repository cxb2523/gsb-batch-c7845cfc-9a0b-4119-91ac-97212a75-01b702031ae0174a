import re
import yaml
import os
import validators
import shutil
from airbyte_connector_generator_poc.logger import logger


SKYFFEL_DIR_NAME = ".skyffel"

ENV_PATH = os.path.join(os.getcwd(), ".env")


def get_skyffel_dir() -> str:
    """Resolve the debug artifact directory for the current working directory."""
    return os.path.join(os.getcwd(), SKYFFEL_DIR_NAME)


# Kept for backwards compatibility with code that imports SKYFFEL_DIR directly.
SKYFFEL_DIR = get_skyffel_dir()


GENERATED_OUTPUT_FILES = ("openapi.yaml", "airbyte_connector.yaml")


def extract_yaml_from_markdown(content) -> dict:
    pattern = r"```yaml(.*?)```"
    matches = re.findall(pattern, content, re.DOTALL)

    if matches:
        yaml_content = matches[0]
    else:
        yaml_content = content
    try:
        return yaml.safe_load(yaml_content.strip())
    except Exception as e:
        write_debug_file("error_extracting_yaml.txt", content)
        logger.debug(f"Error extracting YAML: {yaml_content}")
        raise e


def check_env_for_key(env_key):
    if not os.path.exists(ENV_PATH):
        open(ENV_PATH, 'a').close()
        logger.debug("Notice: .env file was missing and has been created.")

    with open(ENV_PATH, 'r') as env_file:
        lines = env_file.readlines()
        for line in lines:
            if line.strip().startswith(f'{env_key}='):
                return True
        return False


def write_env_variable(key, value):
    with open(ENV_PATH, 'a') as env_file:
        env_file.write(f"{key}={value}\n")
        logger.debug(f"{key} added to .env file.")


def validate_urls(urls: list[str]):
    urls = [url.strip() for url in urls]
    invalid_urls = [url for url in urls if not validators.url(url)]
    if invalid_urls:
        raise ValueError(f"Invalid URLs found: {invalid_urls}")
    return urls


def split_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    """Split URLs into ``(valid, invalid)`` instead of rejecting the batch.

    An unusable URL should cost only that page, so callers report the invalid
    ones as per-page failures and scrape the rest.
    """
    urls = [url.strip() for url in urls]
    valid = [url for url in urls if validators.url(url)]
    invalid = [url for url in urls if not validators.url(url)]
    return valid, invalid


def write_debug_file(file_name, content):
    skyffel_dir = get_skyffel_dir()
    if not os.path.exists(skyffel_dir):
        os.makedirs(skyffel_dir)
    file_path = os.path.join(skyffel_dir, file_name)
    # Explicit UTF-8: scraped docs can contain any Unicode and the locale
    # encoding (e.g. GBK on Windows) would raise mid-run.
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(content)
    logger.debug(f"Debug file written: {file_path}")


def nuke_debug_directory():
    skyffel_dir = get_skyffel_dir()
    if not os.path.exists(skyffel_dir):
        return
    for item in os.listdir(skyffel_dir):
        item_path = os.path.join(skyffel_dir, item)
        if item != "selector_cache.json":
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.unlink(item_path)
            else:
                shutil.rmtree(item_path)
    logger.debug("Debug directory reset.")


def remove_stale_output_files():
    """Delete generated connectors from a previous run.

    When every page fails (or preprocessing happens before generation), a stale
    ``openapi.yaml``/``airbyte_connector.yaml`` from an earlier run would look
    like a successful result. Remove them up front.
    """
    for file_name in GENERATED_OUTPUT_FILES:
        file_path = os.path.join(os.getcwd(), file_name)
        if os.path.isfile(file_path):
            os.unlink(file_path)
