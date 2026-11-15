import logging

from src import session
from src.outcomes import SourceFetchError, VersionNotFoundError
from bs4 import BeautifulSoup

# Define a standard browser User-Agent to avoid 403 Forbidden errors
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://apkpure.net/'
}

def _is_absent_status(exc: Exception) -> bool:
    """404/410 from a mirror means the page genuinely isn't there."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (404, 410)

def get_latest_version(app_name: str, config: dict) -> str:
    url = f"https://apkpure.net/{config['name']}/{config['package']}/versions"

    try:
        # Added headers to the request
        response = session.get(url, headers=HEADERS)
        response.raise_for_status()
    except Exception as e:
        if _is_absent_status(e):
            raise VersionNotFoundError(
                f"APKPure has no versions page for {app_name}: {e}",
                stage="latest_version",
            ) from e
        # Transport / server failure: this source errored, it did not prove absence.
        raise SourceFetchError(
            f"APKPure versions page fetch failed for {app_name}: {e}",
            stage="latest_version",
        ) from e

    content_size = len(response.content)
    logging.info(f"URL:{response.url} [{content_size}/{content_size}] -> \"-\" [1]")

    try:
        soup = BeautifulSoup(response.content, "html.parser")
        version_info = soup.find('div', class_='ver-top-down')

        if version_info and 'data-dt-version' in version_info.attrs:
            return version_info['data-dt-version']
    except Exception as e:
        raise SourceFetchError(
            f"APKPure versions page parse failed for {app_name}: {e}",
            stage="latest_version",
        ) from e

    raise VersionNotFoundError(
        f"APKPure has no version listed for {app_name}", stage="latest_version"
    )

def get_download_link(version: str, app_name: str, config: dict) -> str:
    # APKPure often uses a specific structure for download pages
    url = f"https://apkpure.net/{config['name']}/{config['package']}/download/{version}"

    try:
        response = session.get(url, headers=HEADERS)
        response.raise_for_status()
    except Exception as e:
        if _is_absent_status(e):
            raise VersionNotFoundError(
                f"APKPure has no download page for {app_name} v{version}: {e}",
                stage="download_link",
            ) from e
        raise SourceFetchError(
            f"APKPure download page fetch failed for {app_name} v{version}: {e}",
            stage="download_link",
        ) from e

    content_size = len(response.content)
    logging.info(f"URL:{response.url} [{content_size}/{content_size}] -> \"-\" [1]")

    try:
        soup = BeautifulSoup(response.content, "html.parser")

        # Look for the download link; APKPure sometimes uses 'download_link' or 'fast-download'
        download_link = soup.find('a', id='download_link')
        if download_link:
            return download_link['href']
    except Exception as e:
        raise SourceFetchError(
            f"APKPure download page parse failed for {app_name} v{version}: {e}",
            stage="download_link",
        ) from e

    raise VersionNotFoundError(
        f"APKPure has no download link for {app_name} v{version}", stage="download_link"
    )
