import json
import logging
from pathlib import Path
from src import (
    utils,
    apkpure,
    session,
    uptodown,
    aptoide,
    apkmirror,
    outcomes,
)
from src.outcomes import (
    DownloadReport,
    OutcomeStatus,
    SourceFetchError,
    SourceOutcome,
    VersionNotFoundError,
    collect_outcomes,
)

# Tried in this order by run_build; an explicit "version" in the app config is
# used for every source, otherwise each source is asked for its latest.
PLATFORM_MODULES = {
    "apkmirror": apkmirror,
    "apkpure": apkpure,
    "uptodown": uptodown,
    "aptoide": aptoide,
}
DEFAULT_PLATFORMS = ("apkmirror", "apkpure", "uptodown", "aptoide")

def download_resource(url: str, name: str = None) -> Path:
    try:
        res = session.get(url, stream=True)
        res.raise_for_status()
    except Exception as e:
        raise SourceFetchError(f"could not fetch {url}: {e}", stage="download") from e
    final_url = res.url

    if not name:
        name = utils.extract_filename(res, fallback_url=final_url)

    filepath = Path(name)
    total_size = int(res.headers.get('content-length', 0))
    downloaded_size = 0

    with filepath.open("wb") as file:
        for chunk in res.iter_content(chunk_size=8192):
            if chunk:
                file.write(chunk)
                downloaded_size += len(chunk)

    logging.info(
        f"URL: {final_url} [{downloaded_size}/{total_size}] -> \"{filepath}\" [1]"
    )

    return filepath

def download_required(source: str) -> tuple[list[Path], str]:
    source_path = Path("sources") / f"{source}.json"
    with source_path.open() as json_file:
        repos_info = json.load(json_file)

    # Handle bundle format
    if isinstance(repos_info, dict) and "bundle_url" in repos_info:
        return download_from_bundle(repos_info)
    
    # Handle old list format
    name = repos_info[0]["name"]
    downloaded_files = []

    for repo_info in repos_info[1:]:
        user = repo_info['user']
        repo = repo_info['repo']
        tag = repo_info['tag']

        release = utils.detect_github_release(user, repo, tag)
        
        # Special handling for Morphe files
        if repo == "morphe-patches" or repo == "morphe-cli":
            for asset in release["assets"]:
                if asset["name"].endswith(".asc"):
                    continue
                # Download .mpp patches or morphe-cli.jar
                if asset["name"].endswith(".mpp") or ("morphe-cli" in asset["name"] and asset["name"].endswith(".jar")):
                    filepath = download_resource(asset["browser_download_url"])
                    downloaded_files.append(filepath)
        else:
            # Original logic for ReVanced files
            for asset in release["assets"]:
                if asset["name"].endswith(".asc"):
                    continue
                filepath = download_resource(asset["browser_download_url"])
                downloaded_files.append(filepath)

    return downloaded_files, name

def download_from_bundle(bundle_info: dict) -> tuple[list[Path], str]:
    """Download resources from a bundle URL"""
    bundle_url = bundle_info["bundle_url"]
    name = bundle_info.get("name", "bundle-patches")
    
    logging.info(f"Downloading bundle from {bundle_url}")
    
    # Download the bundle JSON
    with session.get(bundle_url) as res:
        res.raise_for_status()
        bundle_data = res.json()
    
    downloaded_files = []
    
    # Check API version and structure
    if "patches" in bundle_data:
        # API v4 format
        patches = bundle_data.get("patches", [])
        integrations = bundle_data.get("integrations", [])
        
        # Download patches (JAR files)
        for patch in patches:
            if "url" in patch:
                filepath = download_resource(patch["url"])
                downloaded_files.append(filepath)
                logging.info(f"Downloaded patch: {patch.get('name', 'unknown')}")
        
        # Download integrations (APK files)
        for integration in integrations:
            if "url" in integration:
                filepath = download_resource(integration["url"])
                downloaded_files.append(filepath)
                logging.info(f"Downloaded integration: {integration.get('name', 'unknown')}")
    
    # Also download CLI (still needed) - try ReVanced CLI first
    try:
        cli_release = utils.detect_github_release("revanced", "revanced-cli", "latest")
        for asset in cli_release["assets"]:
            if asset["name"].endswith(".asc"):
                continue
            if asset["name"].endswith(".jar") and "cli" in asset["name"].lower():
                filepath = download_resource(asset["browser_download_url"])
                downloaded_files.append(filepath)
                logging.info("Downloaded ReVanced CLI")
                break
    except Exception as e:
        logging.warning(f"Could not download ReVanced CLI: {e}")
    
    return downloaded_files, name

def _load_app_config(app_name: str, platform: str, arch: str = None) -> dict:
    config_path = Path("apps") / platform / f"{app_name}.json"
    if not config_path.exists():
        # A missing local config is a setup problem, not proof the mirror lacks
        # the app/version, so it is an error rather than a not-found outcome.
        raise SourceFetchError(
            f"config file not found: {config_path}", stage="config"
        )

    try:
        with config_path.open() as json_file:
            config = json.load(json_file)
    except Exception as e:
        raise SourceFetchError(
            f"could not read config {config_path}: {e}", stage="config"
        ) from e

    # Override arch if specified
    if arch:
        config['arch'] = arch
    return config


def _resolve_latest_version(platform_module, app_name: str, config: dict) -> str:
    """Ask one source for its latest version.

    Sources signal "no version" by returning ``None``; that becomes
    ``not_found`` for this source. Real transport/parse failures are left to
    propagate so they are reported as errors rather than as absence.
    """
    return platform_module.get_latest_version(app_name, config)


def _resolve_supported_version(config: dict, cli: str, patches: str) -> str | None:
    """Query the patch bundle for supported versions.

    Failures here are reported as this source's error (stage
    ``supported_version``) rather than silently switching to a different
    version, which would change which APK gets patched. ``SystemExit`` from
    ``utils.run_process`` is handled by the caller's catch.
    """
    return utils.get_supported_version(config['package'], cli, patches)


def download_from_source(
    app_name: str,
    platform: str,
    cli: str,
    patches: str,
    arch: str = None,
    *,
    platform_modules: dict | None = None,
    downloader_func=None,
) -> SourceOutcome:
    """Try exactly one source and always return a checkable outcome.

    Version semantics are unchanged:

    * a pinned ``version`` in the app config wins for every source;
    * otherwise the patch-supported version is asked first and each source's
      latest version is the fallback.

    Every failure is attached to this source's outcome with the stage that
    failed (``config`` / ``supported_version`` / ``latest_version`` /
    ``download_link`` / ``download``).
    """

    modules = PLATFORM_MODULES if platform_modules is None else platform_modules
    fetch = download_resource if downloader_func is None else downloader_func
    platform_module = modules.get(platform)
    if platform_module is None:
        return SourceOutcome(
            platform=platform,
            status=OutcomeStatus.ERROR,
            stage="platform",
            detail=f"unknown download platform module: {platform!r}",
        )

    stage = "config"
    try:
        config = _load_app_config(app_name, platform, arch)

        stage = "version"
        version = config.get("version")
        if not version:
            stage = "supported_version"
            version = _resolve_supported_version(config, cli, patches)
            if not version:
                stage = "latest_version"
                version = _resolve_latest_version(platform_module, app_name, config)
            if not version:
                raise VersionNotFoundError(
                    f"no version to request for {config['package']}", stage=stage
                )

        stage = "download_link"
        download_link = platform_module.get_download_link(version, app_name, config)
        if not download_link:
            raise VersionNotFoundError(
                f"no download link for {app_name} v{version}", stage=stage
            )

        stage = "download"
        filepath = fetch(download_link)
        if not filepath or not Path(filepath).exists():
            raise SourceFetchError(
                f"download produced no file for {app_name} v{version}", stage=stage
            )
        if Path(filepath).stat().st_size == 0:
            raise SourceFetchError(
                f"download produced an empty file for {app_name} v{version}", stage=stage
            )

        return SourceOutcome(
            platform=platform,
            status=OutcomeStatus.SUCCESS,
            version=str(version),
            apk=Path(filepath),
        )

    except (Exception, SystemExit) as exc:  # noqa: BLE001 - this source's failure must stay local
        status, failed_stage, detail = outcomes.classify_exception(exc, default_stage=stage)
        return SourceOutcome(
            platform=platform,
            status=status,
            stage=failed_stage,
            detail=detail,
        )


def download_platform(
    app_name: str,
    platform: str,
    cli: str,
    patches: str,
    arch: str = None,
) -> SourceOutcome:
    """Backwards-compatible single-source entry point.

    Returns a :class:`SourceOutcome`; it unpacks as ``(apk, version)`` like the
    old tuple so existing call sites keep working.
    """

    return download_from_source(app_name, platform, cli, patches, arch)


def download_app(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = None,
    *,
    platforms=DEFAULT_PLATFORMS,
    platform_modules: dict | None = None,
    downloader_func=None,
) -> DownloadReport:
    """Try every source in order and return per-source outcomes.

    A failing source never aborts the round; the first success wins and
    remaining sources are skipped. When all sources fail, the report carries
    one outcome per attempted source instead of a bare ``None``.
    """

    report = collect_outcomes(
        platforms,
        lambda platform: download_from_source(
            app_name,
            platform,
            cli,
            patches,
            arch,
            platform_modules=platform_modules,
            downloader_func=downloader_func,
        ),
        app_name=app_name,
    )
    report.log()
    return report


# Update the specific download functions
def download_apkmirror(app_name: str, cli: str, patches: str, arch: str = None) -> SourceOutcome:
    return download_platform(app_name, "apkmirror", cli, patches, arch)

def download_apkpure(app_name: str, cli: str, patches: str, arch: str = None) -> SourceOutcome:
    return download_platform(app_name, "apkpure", cli, patches, arch)

def download_aptoide(app_name: str, cli: str, patches: str, arch: str = None) -> SourceOutcome:
    return download_platform(app_name, "aptoide", cli, patches, arch)

def download_uptodown(app_name: str, cli: str, patches: str, arch: str = None) -> SourceOutcome:
    return download_platform(app_name, "uptodown", cli, patches, arch)

def download_apkeditor() -> Path:
    release = utils.detect_github_release("REAndroid", "APKEditor", "latest")

    for asset in release["assets"]:
        if asset["name"].startswith("APKEditor") and asset["name"].endswith(".jar"):
            return download_resource(asset["browser_download_url"])

    raise RuntimeError("APKEditor .jar file not found in the latest release")
