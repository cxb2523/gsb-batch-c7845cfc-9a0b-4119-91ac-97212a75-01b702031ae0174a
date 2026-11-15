import base64
from typing import Dict

from src import session
from src.outcomes import SourceFetchError, VersionNotFoundError

BASE_URL = "https://ws75.aptoide.com/api/7/"

def _get_json(url: str, stage: str, what: str) -> dict:
    """GET a JSON endpoint, attributing transport/parse failures to the source."""
    try:
        response = session.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise SourceFetchError(f"Aptoide {what} request failed: {e}", stage=stage) from e

def get_latest_version(app_name: str, config: Dict) -> str:
    package = config['package']
    arch = config.get('arch', 'universal')
    q = _get_q_param(arch)
    url = f"{BASE_URL}apps/search?query={package}&limit=1&trusted=true{q}"
    res = _get_json(url, "latest_version", f"app search for {package}")
    try:
        apps = res['datalist']['list']
    except (KeyError, TypeError) as e:
        raise SourceFetchError(
            f"Aptoide app search response malformed for {package}: {e}",
            stage="latest_version",
        ) from e
    if apps:
        return apps[0]['file']['vername']
    raise VersionNotFoundError(f"No version found for {package}", stage="latest_version")

def get_download_link(version: str, app_name: str, config: Dict) -> str:
    package = config['package']
    arch = config.get('arch', 'universal')
    q = _get_q_param(arch)

    if version.lower() == "latest":
        url = f"{BASE_URL}apps/search?query={package}&limit=1&trusted=true{q}"
        res = _get_json(url, "download_link", f"app search for {package}")
        try:
            return res['datalist']['list'][0]['file']['path']
        except (KeyError, IndexError, TypeError) as e:
            raise VersionNotFoundError(
                f"Aptoide has no app entry for {package}: {e}", stage="download_link"
            ) from e

    # Find vercode for specific version
    url_versions = f"{BASE_URL}listAppVersions?package_name={package}&limit=50{q}"
    res_v = _get_json(url_versions, "download_link", f"version list for {package}")
    try:
        versions = res_v['datalist']['list']
    except (KeyError, TypeError) as e:
        raise SourceFetchError(
            f"Aptoide version list response malformed for {package}: {e}",
            stage="download_link",
        ) from e

    vercode = None
    for app in versions:
        if app['file']['vername'] == version:
            vercode = app['file']['vercode']
            break
    if not vercode:
        raise VersionNotFoundError(
            f"Version {version} not found for {package}", stage="download_link"
        )

    # Get meta with download path
    url_meta = f"{BASE_URL}getAppMeta?package_name={package}&vercode={vercode}{q}"
    res_meta = _get_json(url_meta, "download_link", f"app meta for {package}@{vercode}")
    try:
        return res_meta['data']['file']['path']
    except (KeyError, TypeError) as e:
        raise SourceFetchError(
            f"Aptoide app meta response malformed for {package}@{vercode}: {e}",
            stage="download_link",
        ) from e

def _get_q_param(arch: str) -> str:
    if arch == 'universal':
        return ''
    cpu_map = {
        'arm64-v8a': 'arm64-v8a,armeabi-v7a,armeabi',
        'armeabi-v7a': 'armeabi-v7a,armeabi',
        # Add others as needed
    }
    cpu = cpu_map.get(arch, '')
    if cpu:
        q_str = f"myCPU={cpu}&leanback=0"
        return f"&q={base64.b64encode(q_str.encode('utf-8')).decode('utf-8')}"
    return ''
