"""Structured outcomes for the multi-source APK download chain.

Every source the chain tries must leave a checkable outcome:

* ``success``   - an APK was resolved and downloaded,
* ``not_found`` - the source was reached but has no such app/version,
* ``error``     - fetching or parsing the source failed.

The old ``download_platform`` collapsed the two failure kinds into
``(None, None)`` and logged one unattributed "Unexpected error" line, which
made it impossible to tell *which* mirror failed and at which step.  The
helpers here keep the distinctions so callers can log a per-source build
summary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable


class OutcomeStatus(str, Enum):
    """Final disposition of a single source attempt."""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    ERROR = "error"


class SourceError(Exception):
    """A source failed while fetching/parsing its data.

    ``stage`` records where in the attempt it failed (``config``,
    ``supported_version``, ``latest_version``, ``download_link``,
    ``download``, ...) so the log line is actionable.
    """

    outcome_status = OutcomeStatus.ERROR

    def __init__(self, message: str = "", *, stage: str = "source"):
        super().__init__(message or self.__class__.__name__)
        self.stage = stage


class SourceFetchError(SourceError):
    """The source could not be fetched or parsed."""

    outcome_status = OutcomeStatus.ERROR


class VersionNotFoundError(SourceError):
    """The source is reachable but does not have the requested app/version."""

    outcome_status = OutcomeStatus.NOT_FOUND


def classify_exception(exc: BaseException, default_stage: str = "source") -> tuple[OutcomeStatus, str, str]:
    """Map an arbitrary exception onto ``(status, stage, detail)``.

    Exceptions raised by another copy of this module (e.g. when the package is
    imported under both ``src`` and its folder name) are still recognised via
    their ``outcome_status`` attribute rather than ``isinstance``.
    """

    raw_status = getattr(exc, "outcome_status", None)
    if raw_status is None:
        status = OutcomeStatus.ERROR
    else:
        try:
            status = OutcomeStatus(getattr(raw_status, "value", raw_status))
        except ValueError:
            status = OutcomeStatus.ERROR
    stage = getattr(exc, "stage", None) or default_stage
    if isinstance(exc, SystemExit):
        # utils.run_process exits when a helper binary is missing.
        detail = f"process exited with code {exc.code}"
    else:
        detail = str(exc) or exc.__class__.__name__
    return status, stage, detail


@dataclass
class SourceOutcome:
    """What happened when one mirror was tried."""

    platform: str
    status: OutcomeStatus
    stage: str = ""
    detail: str = ""
    version: str | None = None
    apk: Path | None = None

    def __post_init__(self) -> None:
        # Accept a plain string / a status enum from another module copy.
        if not isinstance(self.status, OutcomeStatus):
            self.status = OutcomeStatus(getattr(self.status, "value", self.status))
        if self.apk is not None and not isinstance(self.apk, Path):
            self.apk = Path(self.apk)

    @property
    def ok(self) -> bool:
        return self.status == OutcomeStatus.SUCCESS

    # Backwards compatibility: old callers did ``apk, version = method(...)``.
    def __iter__(self):
        yield self.apk
        yield self.version

    def describe(self) -> str:
        parts = [f"{self.platform}: {self.status.value}"]
        if self.stage:
            parts.append(f"({self.stage})")
        if self.version:
            parts.append(f"v{self.version}")
        if self.detail:
            parts.append(f"- {self.detail}")
        return " ".join(parts)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.describe()

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "status": self.status.value,
            "stage": self.stage,
            "version": self.version,
            "apk": str(self.apk) if self.apk is not None else None,
            "detail": self.detail,
        }


@dataclass
class DownloadReport:
    """Per-source outcomes for one app, plus the winning APK (if any)."""

    app_name: str
    outcomes: list[SourceOutcome] = field(default_factory=list)

    @property
    def success(self) -> SourceOutcome | None:
        return next((o for o in self.outcomes if o.ok), None)

    @property
    def apk(self) -> Path | None:
        success = self.success
        return success.apk if success else None

    @property
    def version(self) -> str | None:
        success = self.success
        return success.version if success else None

    @property
    def all_failed(self) -> bool:
        return self.success is None

    def format_summary(self) -> str:
        if not self.outcomes:
            return "no sources attempted"
        return "; ".join(outcome.describe() for outcome in self.outcomes)

    def as_dict(self) -> dict:
        return {
            "app": self.app_name,
            "success": self.success.platform if self.success else None,
            "apk": str(self.apk) if self.apk is not None else None,
            "version": self.version,
            "sources": [outcome.as_dict() for outcome in self.outcomes],
        }

    def log(self, logger: logging.Logger | None = None) -> None:
        """Log the round's verdict; per-source lines are logged as they finish."""
        log = logger or logging.getLogger(__name__)
        if self.all_failed:
            log.error("%s: every download source failed: %s", self.app_name, self.format_summary())
        else:
            chosen = self.success
            log.info(
                "%s: downloaded from %s (v%s): %s",
                self.app_name,
                chosen.platform,
                chosen.version,
                self.format_summary(),
            )

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.format_summary()


def log_outcome(outcome: SourceOutcome) -> None:
    """Log one attempt as soon as it finishes, with an appropriate level."""

    if outcome.ok:
        logging.info("✓ %s", outcome.describe())
    elif outcome.status == OutcomeStatus.NOT_FOUND:
        logging.warning("∅ %s", outcome.describe())
    else:
        logging.error("✗ %s", outcome.describe())


def as_outcome(platform: str, value) -> SourceOutcome:
    """Coerce an attempt result into a :class:`SourceOutcome`.

    Accepts the outcome type itself, or any duck-typed object carrying
    ``platform``/``status`` (which can happen if this module was imported
    under two different names). Anything else is a programming error.
    """

    if isinstance(value, SourceOutcome):
        return value
    if hasattr(value, "status") and hasattr(value, "platform"):
        return SourceOutcome(
            platform=getattr(value, "platform", platform),
            status=getattr(value, "status"),
            stage=getattr(value, "stage", ""),
            detail=getattr(value, "detail", ""),
            version=getattr(value, "version", None),
            apk=getattr(value, "apk", None),
        )
    raise TypeError(
        f"attempt({platform!r}) returned {type(value).__name__}, expected SourceOutcome"
    )


def collect_outcomes(
    platforms: Iterable[str],
    attempt: Callable[[str], SourceOutcome],
    app_name: str = "",
) -> DownloadReport:
    """Run ``attempt`` for each platform in order, never aborting the round.

    A source that raises (typed, untyped, or ``SystemExit`` from a helper
    binary) becomes a failed outcome and the next source is still tried. The
    loop stops only after a successful attempt, so every platform up to and
    including the winner has exactly one outcome.
    """

    outcomes: list[SourceOutcome] = []
    for platform in platforms:
        try:
            outcome = as_outcome(platform, attempt(platform))
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - one bad source must not kill the chain
            status, stage, detail = classify_exception(exc)
            outcome = SourceOutcome(
                platform=platform,
                status=status,
                stage=stage,
                detail=detail,
            )
        outcomes.append(outcome)
        log_outcome(outcome)
        if outcome.ok:
            break
    return DownloadReport(app_name=app_name, outcomes=outcomes)
