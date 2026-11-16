'''
Date: 2023-10-23 18:24:31
Description: Per-subscription free-score collection with isolated failure handling.

Each subscription line has the form "source|author". Fetching/downloading for
one author must never break the processing of the remaining authors: a fetch
exception, a failed download or an empty result is recorded for that author
only, while its previously persisted progress is left untouched.
'''

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# outcome statuses
SUCCESS = "success"
EMPTY = "empty"
FETCH_ERROR = "fetch_error"
DOWNLOAD_FAILED = "download_failed"
NO_HANDLER = "no_handler"
INVALID_LINE = "invalid_line"

FAILURE_STATUSES = (FETCH_ERROR, DOWNLOAD_FAILED, NO_HANDLER, INVALID_LINE)


def parse_last_download(lines):
    """Parse the "_last_download_signal" file into {md5: sheet_id}."""
    last_download = {}
    for line in lines:
        md5, timestamp = line.strip().split(" ")
        last_download[md5] = float(timestamp)
    return last_download


def subscription_md5(raw_line: str) -> str:
    """md5 key for a subscription line.

    Mirrors the historical implementation: the "source|author" line is
    split on "|" and the parts are joined back together (i.e. the "|"
    separators are removed) before hashing, so existing entries in
    _last_download_signal remain valid.
    """
    parts = raw_line.strip().split("|")
    return hashlib.md5("".join(parts).encode("utf-8")).hexdigest()


@dataclass
class SubscriptionOutcome:
    """Result of processing a single subscription line."""

    md5: str
    source: str
    user: str
    status: str
    sheet_count: int = 0
    max_sheetnum: Optional[int] = None
    titles: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.status in FAILURE_STATUSES


def process_subscriptions(
    subscription_lines,
    last_downloads,
    handler_resolver: Callable[[str], object],
    error_collector: Optional[Callable[[str], None]] = None,
):
    """Process every subscription independently.

    A fetch exception, a failed download or an empty result only marks the
    current author as failed/empty; the loop always continues with the
    remaining authors.

    Args:
        subscription_lines: raw lines from the subscriptions file.
        last_downloads: {md5: max_processed_sheet_id} loaded from the
            progress file.
        handler_resolver: callable mapping a source name to its handler
            (e.g. auto_score.utils.singleton.get_handler).
        error_collector: optional callback receiving human-readable error
            messages (e.g. main.collect_errors).

    Returns:
        List[SubscriptionOutcome], one outcome per well-formed line.
    """
    outcomes = []

    for raw_line in subscription_lines:
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split("|")
        if len(parts) < 2:
            msg = f"Invalid subscription line, expected 'source|author': {line!r}"
            logger.error(msg)
            if error_collector:
                error_collector(msg)
            # no usable md5/source, nothing can be persisted for this line
            outcomes.append(
                SubscriptionOutcome(
                    md5="", source="", user="", status=INVALID_LINE, error=msg
                )
            )
            continue

        source_name = parts[0]
        user = parts[1]
        md5 = subscription_md5(line)
        last_sheetnum = last_downloads.get(md5, -1)

        try:
            handler = handler_resolver(source_name)
            if handler is None:
                raise RuntimeError(
                    f"no handler registered for RSS source {source_name}"
                )

            links, max_sheetnum, titles = handler.get_recent_sheets(
                user, int(last_sheetnum)
            )

            if not links:  # nothing new for this author
                logger.warning(
                    "No new link found for %s's sheets in RSS source %s.",
                    user,
                    source_name,
                )
                outcomes.append(
                    SubscriptionOutcome(
                        md5=md5,
                        source=source_name,
                        user=user,
                        status=EMPTY,
                        max_sheetnum=max_sheetnum,
                        titles=titles,
                    )
                )
                continue

            is_download_success = handler.download_sheets(links)
            if is_download_success:
                logger.info(
                    "Successfully downloaded %d link(s) for %s (source %s).",
                    len(links),
                    user,
                    source_name,
                )
                outcomes.append(
                    SubscriptionOutcome(
                        md5=md5,
                        source=source_name,
                        user=user,
                        status=SUCCESS,
                        sheet_count=len(links),
                        max_sheetnum=max_sheetnum,
                        titles=titles,
                    )
                )
            else:  # download reported failure
                msg = (
                    f"Failed when downloading {user}'s sheets in RSS "
                    f"source {source_name}."
                )
                logger.error(msg)
                if error_collector:
                    error_collector(msg)
                outcomes.append(
                    SubscriptionOutcome(
                        md5=md5,
                        source=source_name,
                        user=user,
                        status=DOWNLOAD_FAILED,
                        error=msg,
                    )
                )

        except Exception as exc:  # fetch/handler error for this author only
            msg = (
                f"Error when fetching {user}'s sheets in RSS source "
                f"{source_name}: {exc!r}"
            )
            logger.exception(msg)
            if error_collector:
                error_collector(msg)
            outcomes.append(
                SubscriptionOutcome(
                    md5=md5,
                    source=source_name,
                    user=user,
                    status=FETCH_ERROR,
                    error=msg,
                )
            )

    return outcomes


def build_progress_updates(outcomes, last_downloads):
    """Select the progress entries to persist for this run.

    Only subscriptions actually processed this run are considered:
      * successful authors advance to the max sheetId just processed;
      * failed authors keep their previously persisted value (never 0,
        never deleted, never overwritten by an empty result);
      * empty-result authors keep their previously persisted value as
        well, and a brand-new subscription with no prior value is not
        written at all.
    """
    updates = {}
    for outcome in outcomes:
        if not outcome.md5:
            continue  # invalid line: no key to persist

        previous = last_downloads.get(outcome.md5)
        if outcome.status == SUCCESS:
            updates[outcome.md5] = outcome.max_sheetnum
        elif previous is not None:
            # failure or empty result: carry the old progress forward
            updates[outcome.md5] = previous
        # else: failed/empty subscription that was never persisted before
    return updates


def write_last_download_signal(last_download_filename, updates):
    """Persist progress updates, skipping falsy values as before."""
    with open(last_download_filename, "w") as file:
        for md5, max_sheetnum in updates.items():
            if max_sheetnum:
                file.write(f"{md5} {max_sheetnum}\n")
    logger.info("Update last download signal successfully.")
