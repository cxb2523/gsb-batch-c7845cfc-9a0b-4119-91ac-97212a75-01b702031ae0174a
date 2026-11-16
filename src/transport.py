import pandas as pd
from typing import Dict, Any, List, Optional, Tuple
import logging
import os
import random
import time
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from src.utils import TokenBucket
from src.config_models import ProjectConfigs

# Set up a logger for this module
logger = logging.getLogger(__name__)

# The three terminal states a task can settle in. Every task submitted to
# HttpTransport.fetch ends in exactly one of these.
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# The fields guaranteed to be present on every settlement record.
SETTLEMENT_FIELDS = ("task_id", "interface_id", "source_domain", "status", "reason")


class FetchReport(list):
    """
    The aggregated outcome of a single ``HttpTransport.fetch`` call.

    It behaves like a list of settlement records (one record per task in the
    input manifest, in manifest order), so callers can iterate it directly::

        for record in report:
            if record["status"] == "failed":
                ...

    Each record always contains ``task_id``, ``interface_id``,
    ``source_domain``, ``status`` (``success`` / ``failed`` / ``skipped``) and a
    human-readable ``reason``. Successful records additionally carry the
    ``task`` and ``raw_file_path`` needed by downstream stages.

    For convenience the report also exposes the historical fetch-result view
    (successful results only) via :attr:`successes` / :attr:`fetch_results`.
    """

    _STRING_KEYS = frozenset(
        {
            "records",
            "settlements",
            "results",
            "successes",
            "successful",
            "fetch_results",
            "failed",
            "skipped",
            "summary",
            "counts",
            "total",
        }
    )

    def __init__(
        self,
        records: Optional[List[Dict[str, Any]]] = None,
        successes: Optional[List[Dict[str, Any]]] = None,
    ):
        records = list(records or [])
        super().__init__(records)
        self._settlements = records
        # Allow callers to pass either view; prefer explicit ``successes`` and
        # otherwise derive them from the success settlement records.
        if successes is None:
            successes = [
                {"task": r.get("task"), "raw_file_path": r["raw_file_path"]}
                for r in records
                if r.get("status") == STATUS_SUCCESS and "raw_file_path" in r
            ]
        self._successes = list(successes)

    # ------------------------------------------------------------------
    # Collection views
    # ------------------------------------------------------------------
    @property
    def records(self) -> List[Dict[str, Any]]:
        """All settlement records, in manifest order."""
        return list(self._settlements)

    @property
    def settlements(self) -> List[Dict[str, Any]]:
        """Alias for :attr:`records`."""
        return self.records

    @property
    def successes(self) -> List[Dict[str, Any]]:
        """Fetch results (``task`` + ``raw_file_path``) for successful tasks only.

        This mirrors the list historically returned by ``HttpTransport.fetch``
        and is the shape consumed by ``Normalizer.process``.
        """
        return list(self._successes)

    @property
    def fetch_results(self) -> List[Dict[str, Any]]:
        """Alias for :attr:`successes`."""
        return self.successes

    @property
    def failed_records(self) -> List[Dict[str, Any]]:
        return [r for r in self._settlements if r.get("status") == STATUS_FAILED]

    @property
    def skipped_records(self) -> List[Dict[str, Any]]:
        return [r for r in self._settlements if r.get("status") == STATUS_SKIPPED]

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------
    @property
    def summary(self) -> Dict[str, int]:
        """Counts per status plus the total number of settled tasks."""
        counts = Counter(r.get("status") for r in self._settlements)
        return {
            "total": len(self._settlements),
            STATUS_SUCCESS: counts.get(STATUS_SUCCESS, 0),
            STATUS_FAILED: counts.get(STATUS_FAILED, 0),
            STATUS_SKIPPED: counts.get(STATUS_SKIPPED, 0),
        }

    @property
    def counts(self) -> Dict[str, int]:
        """Alias for :attr:`summary`."""
        return self.summary

    def get_record(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Returns the settlement record for ``task_id``, if present."""
        task_id = str(task_id)
        for record in self._settlements:
            if record.get("task_id") == task_id:
                return record
        return None

    def to_dataframe(self) -> pd.DataFrame:
        """Returns the settlement records as a flat DataFrame."""
        rows = [{field: r.get(field) for field in SETTLEMENT_FIELDS} for r in self._settlements]
        return pd.DataFrame(rows, columns=list(SETTLEMENT_FIELDS))

    # ------------------------------------------------------------------
    # Lightweight dict-style access for callers that prefer named views
    # ------------------------------------------------------------------
    def __getitem__(self, key):
        if isinstance(key, str):
            views = {
                "records": self.records,
                "settlements": self.settlements,
                "results": self.records,
                "successes": self.successes,
                "successful": self.successes,
                "fetch_results": self.fetch_results,
                "failed": self.failed_records,
                "skipped": self.skipped_records,
                "summary": self.summary,
                "counts": self.counts,
                "total": self.summary["total"],
            }
            if key in views:
                return views[key]
            raise KeyError(key)
        return list.__getitem__(self, key)

    def __contains__(self, item) -> bool:
        if isinstance(item, str) and item in self._STRING_KEYS:
            return True
        return list.__contains__(self, item)

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style lookup for named views; mirror of ``dict.get``."""
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        s = self.summary
        return (
            f"FetchReport(total={s['total']}, success={s[STATUS_SUCCESS]}, "
            f"failed={s[STATUS_FAILED]}, skipped={s[STATUS_SKIPPED]})"
        )


class ReplayTransport:
    """
    A transport layer that "fetches" data by replaying from local files.
    This is used for the offline smoke test to read from the bootstrap data,
    simulating the behavior of a live data fetching component.
    """

    def __init__(self, configs: ProjectConfigs):
        """
        Initializes the ReplayTransport.

        Args:
            configs: The validated configuration object.
        """
        self.configs = configs

    def fetch(self, tasks_df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Reads data from local CSV files, saves them to their designated
        Parquet output paths, and returns a list of task-result dicts.
        This mimics the behavior of HttpTransport for a consistent interface.
        """
        logger.info(f"Replaying data from {len(tasks_df)} tasks specified in the manifest...")

        results = []
        for _, task in tasks_df.iterrows():
            replay_path = task["replay_path"]
            output_path = task["output_path"]
            logger.info(f"  - Replaying '{task['interface_id']}' from {replay_path} -> {output_path}")

            try:
                df = pd.read_csv(replay_path)
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                df.to_parquet(output_path, index=False)
                # The result contains the task metadata and the path to the new raw file
                results.append({"task": task, "raw_file_path": output_path})
            except FileNotFoundError:
                logger.error(f"    - Replay file not found: {replay_path}")
            except Exception as e:
                logger.error(f"    - Failed to process replay file {replay_path}: {e}", exc_info=True)

        num_success = len(results)
        num_total = len(tasks_df)
        logger.info(f"Successfully replayed and saved {num_success}/{num_total} data sources.")

        if num_success == 0 and num_total > 0:
            raise RuntimeError("Failed to process any replay data.")

        return results


class HttpTransport:
    """
    A transport layer that fetches data from live akshare endpoints.
    This class handles online data collection, including proxy rotation,
    domain-specific rate limiting, and robust, resumable checkpointing.

    Every task handled by :meth:`fetch` produces exactly one settlement record
    (``task_id``, ``interface_id``, ``source_domain``, ``status``, ``reason``).
    Only tasks that settle as ``success`` are persisted to the checkpoint file,
    so a rerun retries failures and skips work that is already done.
    """

    def __init__(
        self,
        configs: ProjectConfigs,
        manifest_dir: str,
        raw_data_dir: str,
        ak: Optional[Any] = None,
    ):
        """Initializes the HttpTransport.

        Args:
            configs: The validated configuration object.
            manifest_dir: Directory that holds the checkpoint file.
            raw_data_dir: Root directory for raw data output.
            ak: Optional pre-imported ``akshare`` module (mainly for testing).
        """
        self.configs = configs
        self.raw_data_dir = raw_data_dir
        os.makedirs(manifest_dir, exist_ok=True)
        self.checkpoint_path = os.path.join(manifest_dir, "checkpoints.parquet")
        self.checkpoint_lock = threading.Lock()
        # In-memory mirror of the checkpoint file. Guarded by checkpoint_lock.
        self._completed_task_ids = set()
        # Fingerprint of the checkpoint file at our last read/write, used to
        # merge updates made by other transport instances instead of clobbering
        # them. Guarded by checkpoint_lock.
        self._last_checkpoint_signature = None
        # Base delay for the exponential retry backoff (seconds). Kept as an
        # attribute so tests can disable the wait.
        self.retry_sleep_base = 1.0
        # Per-thread state used to propagate a failure reason from _fetch_one
        # back to the worker that builds the settlement record. Keeping the
        # DataFrame return contract intact means existing stubs/callers of
        # _fetch_one keep working.
        self._thread_state = threading.local()

        if ak is None:
            try:
                import akshare as ak_module
                ak = ak_module
            except ImportError:
                logger.warning(
                    "The 'akshare' library is not installed. Online fetches will be "
                    "recorded as failed settlements until it is available."
                )
        self.ak = ak

        # Proxy and Rate Limiter setup
        self._setup_proxy()
        self._setup_rate_limiters()

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------
    def _setup_proxy(self):
        """Loads proxy credentials from environment variables."""
        self.BRD_HOST = os.environ.get("BRD_HOST", "brd.superproxy.io")
        self.BRD_PORT = int(os.environ.get("BRD_PORT", 33335))
        self.BRD_USERNAME_BASE = os.environ.get("BRD_USERNAME_BASE")
        self.BRD_PASSWORD = os.environ.get("BRD_PASSWORD")
        if not self.BRD_USERNAME_BASE or not self.BRD_PASSWORD:
            raise ValueError("Missing Bright Data proxy credentials in environment variables.")

    def _setup_rate_limiters(self):
        """Initializes domain-specific rate limiters from config."""
        self.rate_limiters = {}
        for domain_config in self.configs.rate_limits.domains:
            self.rate_limiters[domain_config.domain] = TokenBucket(domain_config.capacity, domain_config.rate)
            logger.info(f"Initialized rate limiter for domain '{domain_config.domain}': {domain_config.rate} req/s")

    def _get_domain_config(self, source_domain: str):
        """Gets the configuration for a specific source domain, falling back to default."""
        domain_configs = self.configs.rate_limits.domains
        config = next((d for d in domain_configs if d.domain == source_domain), None)
        if not config:
            config = next((d for d in domain_configs if d.domain == 'default'), None)
        return config

    def _get_rate_limiter(self, source_domain: str):
        """Returns the token bucket for ``source_domain``, falling back to default."""
        limiter = self.rate_limiters.get(source_domain) if source_domain else None
        if limiter is None:
            limiter = self.rate_limiters.get('default')
            if source_domain and source_domain != 'default' and limiter is not None:
                logger.debug(
                    f"No dedicated rate limiter for domain '{source_domain}'; using 'default'."
                )
        return limiter

    def _max_tries_for(self, source_domain: str) -> int:
        """Returns the configured retry budget for ``source_domain`` (or default)."""
        domain_config = self._get_domain_config(source_domain)
        max_tries = domain_config.retry if domain_config else 3
        try:
            max_tries = int(max_tries)
        except (TypeError, ValueError):
            max_tries = 3
        return max(1, max_tries)

    def _set_proxy_session(self) -> None:
        """Sets the HTTP/HTTPS proxy with a new random session ID."""
        session_id = str(random.randint(100000, 999999))
        proxy_user = f"{self.BRD_USERNAME_BASE}-session-{session_id}"
        proxy_url = f"http://{proxy_user}:{self.BRD_PASSWORD}@{self.BRD_HOST}:{self.BRD_PORT}"
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url

    # ------------------------------------------------------------------
    # Task field helpers (tolerate pandas NaN for optional columns)
    # ------------------------------------------------------------------
    @staticmethod
    def _get_task_field(task, key: str, default=None):
        try:
            value = task[key]
        except (KeyError, IndexError, TypeError):
            return default
        if value is None:
            return default
        try:
            if bool(pd.isna(value)):
                return default
        except (TypeError, ValueError):
            # Non-scalar values (e.g. list/dict params) are never "missing".
            pass
        return value

    def _task_id_of(self, task) -> str:
        value = self._get_task_field(task, 'task_id', '')
        if value is pd.NaT:  # pragma: no cover - defensive
            return ''
        return str(value).strip()

    def _task_interface_id(self, task) -> str:
        value = self._get_task_field(task, 'interface_id', '')
        return '' if value is None else str(value)

    def _task_source_domain(self, task) -> str:
        value = self._get_task_field(task, 'source_domain', 'default')
        if value is None or str(value).strip() == '':
            return 'default'
        return str(value)

    def _task_params(self, task) -> Dict[str, Any]:
        value = self._get_task_field(task, 'params', {})
        return value if isinstance(value, dict) else {}

    # ------------------------------------------------------------------
    # Per-thread failure reason propagation
    # ------------------------------------------------------------------
    def _set_last_error(self, reason: str, attempts: int = 0) -> None:
        self._thread_state.last_error = reason
        self._thread_state.attempts = attempts

    def _get_last_error(self) -> Optional[str]:
        return getattr(self._thread_state, 'last_error', None)

    def _get_last_attempts(self) -> int:
        return getattr(self._thread_state, 'attempts', 0)

    def _clear_last_error(self) -> None:
        self._thread_state.last_error = None
        self._thread_state.attempts = 0

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------
    def _fetch_one(self, interface_id: str, params: Dict, source_domain: str) -> pd.DataFrame:
        """
        Fetches data for a single task, wrapped in a domain-configured retry loop.

        Returns the fetched DataFrame, or an empty DataFrame when the retry
        budget is exhausted. The failure reason is stashed on a thread-local so
        the caller can build a readable settlement record without changing the
        DataFrame return contract that existing stubs rely on.
        """
        self._clear_last_error()

        if self.ak is None:
            reason = (
                f"The 'akshare' library is not available; cannot call interface "
                f"'{interface_id}'."
            )
            logger.error(reason)
            self._set_last_error(reason, attempts=0)
            return pd.DataFrame()

        try:
            ak_func = getattr(self.ak, interface_id)
        except AttributeError:
            reason = f"No function named '{interface_id}' found in the akshare library."
            logger.error(reason)
            self._set_last_error(reason, attempts=0)
            return pd.DataFrame()

        max_tries = self._max_tries_for(source_domain)
        sleep_base = self.retry_sleep_base
        last_error: Optional[BaseException] = None

        for attempt in range(max_tries):
            try:
                self._set_proxy_session()
                logger.info(
                    f"Calling endpoint: {ak_func.__name__} with params {params} "
                    f"(Attempt {attempt + 1}/{max_tries})"
                )
                df = ak_func(**params)
                if not isinstance(df, pd.DataFrame):
                    df = pd.DataFrame(df)
                # Keep the attempt count for the settlement record, but clear
                # the error so the task settles as a success.
                self._thread_state.last_error = None
                self._thread_state.attempts = attempt + 1
                return df
            except requests.exceptions.RequestException as e:
                last_error = e
                logger.warning(f"  - Network-level error for {interface_id} on attempt {attempt + 1}: {e}")
            except Exception as e:
                last_error = e
                logger.warning(f"  - General error for {interface_id} on attempt {attempt + 1}: {e}")

            if attempt + 1 < max_tries:
                time.sleep(sleep_base * (2 ** attempt) + random.random())

        if last_error is None:
            reason = (
                f"Retry budget exhausted after {max_tries} attempt(s) for "
                f"'{interface_id}' without returning data."
            )
        else:
            reason = (
                f"Retry budget exhausted after {max_tries} attempt(s): "
                f"{type(last_error).__name__}: {last_error}"
            )
        logger.error(f"  - {reason}")
        self._set_last_error(reason, attempts=max_tries)
        return pd.DataFrame()

    def _resolve_output_path(self, task) -> str:
        """Returns the task's output path, deriving a structured one if absent."""
        output_path = self._get_task_field(task, 'output_path', None)
        if output_path:
            return str(output_path)

        task_id = self._task_id_of(task)
        interface_id = self._task_interface_id(task)
        source_domain = self._task_source_domain(task)
        output_dir = os.path.join(
            self.raw_data_dir,
            f"source_domain={source_domain}",
            f"interface={interface_id}",
            "date=unknown",
        )
        return os.path.join(output_dir, f"part-{task_id[:10]}.parquet")

    # ------------------------------------------------------------------
    # Checkpointing
    # ------------------------------------------------------------------
    def _checkpoint_signature(self):
        """Cheap fingerprint of the checkpoint file, used to detect external writes."""
        try:
            st = os.stat(self.checkpoint_path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _load_checkpoint(self) -> set:
        """Loads the set of completed task IDs (see :meth:`_read_checkpoint`)."""
        completed_ids, _ = self._read_checkpoint()
        return completed_ids

    def _read_checkpoint(self) -> Tuple[set, str]:
        """
        Loads the set of completed task IDs together with the file's state.

        Returns:
            ``(completed_ids, state)`` where ``state`` is one of ``"ok"``,
            ``"missing"`` or ``"corrupt"``. A missing file is an empty
            checkpoint. An unreadable or corrupted file is logged as a warning
            and treated as empty so the batch can continue.
        """
        completed = set()
        if not os.path.exists(self.checkpoint_path):
            logger.info("No checkpoint file found; starting from an empty checkpoint.")
            return completed, "missing"

        try:
            completed_df = pd.read_parquet(self.checkpoint_path)
            if 'task_id' not in getattr(completed_df, 'columns', []):
                raise ValueError("checkpoint file is missing the required 'task_id' column")
            ids = [str(v) for v in completed_df['task_id'].dropna().tolist()]
            completed = set(ids)
            duplicates = len(ids) - len(completed)
            logger.info(f"Loaded {len(completed)} completed tasks from checkpoint file.")
            if duplicates:
                logger.warning(
                    f"Checkpoint file contained {duplicates} duplicate task_id row(s); "
                    f"they were collapsed for this run."
                )
        except Exception as e:
            logger.warning(
                f"Checkpoint file '{self.checkpoint_path}' is unreadable or corrupted "
                f"({type(e).__name__}: {e}). Starting from an empty checkpoint."
            )
            return set(), "corrupt"

        return completed, "ok"

    def _write_checkpoint_locked(self) -> None:
        """
        Atomically writes the in-memory checkpoint set to disk.

        Must be called while holding ``checkpoint_lock``. Writing to a temporary
        file and replacing the destination prevents readers from ever observing
        a partially written checkpoint. If another transport (or process)
        updated the file since our last write, its records are merged in first
        so concurrent writers never lose each other's entries.
        """
        os.makedirs(os.path.dirname(self.checkpoint_path) or '.', exist_ok=True)

        signature = self._checkpoint_signature()
        if signature is not None and signature != self._last_checkpoint_signature:
            # The file changed underneath us; merge those records instead of
            # overwriting them.
            try:
                existing_df = pd.read_parquet(self.checkpoint_path)
                if 'task_id' in getattr(existing_df, 'columns', []):
                    self._completed_task_ids.update(
                        str(v) for v in existing_df['task_id'].dropna().tolist()
                    )
            except Exception as e:
                logger.warning(
                    f"Could not merge external checkpoint updates ({type(e).__name__}: {e}); "
                    f"continuing with the in-memory checkpoint."
                )

        snapshot = pd.DataFrame({'task_id': sorted(self._completed_task_ids)})
        tmp_path = f"{self.checkpoint_path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        try:
            snapshot.to_parquet(tmp_path, engine='pyarrow', index=False)
            os.replace(tmp_path, self.checkpoint_path)
            self._last_checkpoint_signature = self._checkpoint_signature()
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:  # pragma: no cover - best effort cleanup
                    pass

    def _save_and_checkpoint(self, task, df: pd.DataFrame) -> str:
        """
        Saves the dataframe and appends the task ID to the checkpoint file.

        The checkpoint update is serialized by ``checkpoint_lock`` and backed by
        the in-memory set, so concurrent workers cannot lose records or write
        duplicate ``task_id`` values.
        """
        output_path = self._resolve_output_path(task)
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        df.to_parquet(output_path, index=False)

        task_id = self._task_id_of(task)
        with self.checkpoint_lock:
            if task_id not in self._completed_task_ids:
                self._completed_task_ids.add(task_id)
                try:
                    self._write_checkpoint_locked()
                except Exception:
                    # Roll back the in-memory state and let the task settle as
                    # failed: the checkpoint is the source of truth for reruns.
                    self._completed_task_ids.discard(task_id)
                    raise

        logger.info(f"  - Saved and checkpointed task '{task_id[:10]}' to {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # Settlement
    # ------------------------------------------------------------------
    def _build_settlement(
        self,
        task_row,
        status: str,
        reason: str,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Builds a settlement record with the guaranteed fields first.

        ``extra`` may include a ``task=`` entry, which is stored on the record
        for downstream consumers; ``task_row`` is the manifest row itself.
        """
        record: Dict[str, Any] = {
            "task_id": self._task_id_of(task_row),
            "interface_id": self._task_interface_id(task_row),
            "source_domain": self._task_source_domain(task_row),
            "status": status,
            "reason": reason,
        }
        record.update(extra)
        return record

    def _fetch_and_save_task(self, task) -> Dict[str, Any]:
        """Rate-limits, fetches, saves and checkpoints a single task.

        Returns:
            The task's settlement record. It is always produced, even when the
            task fails. Successful records also carry ``task`` and
            ``raw_file_path`` so they can be fed to downstream stages.
        """
        source_domain = self._task_source_domain(task)
        interface_id = self._task_interface_id(task)
        task_id = self._task_id_of(task)
        self._clear_last_error()

        try:
            # Rate limiting is chosen by the task's own source_domain, falling
            # back to the default domain's bucket when there is no exact match.
            limiter = self._get_rate_limiter(source_domain)
            if limiter is not None:
                limiter.consume()

            df = self._fetch_one(interface_id, self._task_params(task), source_domain)
            if not isinstance(df, pd.DataFrame):
                # Tolerate custom fetch implementations returning list/dict data.
                try:
                    df = pd.DataFrame(df)
                except Exception:
                    df = pd.DataFrame()

            if df.empty:
                attempts = self._get_last_attempts()
                reason = self._get_last_error()
                if not reason:
                    if attempts:
                        reason = (
                            f"Fetch returned an empty dataset after {attempts} attempt(s); "
                            f"treated as a failure."
                        )
                    else:
                        reason = (
                            f"Fetch returned an empty dataset (retry budget: "
                            f"{self._max_tries_for(source_domain)} attempt(s)); "
                            f"treated as a failure."
                        )
                return self._build_settlement(
                    task,
                    STATUS_FAILED,
                    reason,
                    attempts=attempts or self._max_tries_for(source_domain),
                    task=task,
                )

            output_path = self._save_and_checkpoint(task, df)
            return self._build_settlement(
                task,
                STATUS_SUCCESS,
                f"Saved {len(df)} row(s) to {output_path}.",
                attempts=self._get_last_attempts(),
                output_path=output_path,
                raw_file_path=output_path,
                task=task,
            )
        except Exception as e:
            logger.error(
                f"  - Task '{task_id[:10]}' ({interface_id} @ {source_domain}) failed: {e}",
                exc_info=True,
            )
            return self._build_settlement(
                task,
                STATUS_FAILED,
                f"{type(e).__name__}: {e}",
                attempts=self._get_last_attempts(),
                task=task,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def fetch(self, tasks_df: pd.DataFrame) -> FetchReport:
        """Processes a manifest of tasks concurrently and returns a settlement report.

        Every task in ``tasks_df`` gets exactly one settlement record. Tasks
        already present in the checkpoint are reported as ``skipped`` and are
        not submitted. Failed tasks are never written to the checkpoint.
        """
        if tasks_df is None:
            tasks_df = pd.DataFrame()

        # Capture the fingerprint *before* reading so that a concurrent writer
        # racing with our load is detected (and merged) at the next save.
        with self.checkpoint_lock:
            signature_before_load = self._checkpoint_signature()

        completed_ids, checkpoint_state = self._read_checkpoint()
        with self.checkpoint_lock:
            self._completed_task_ids = set(completed_ids)
            self._last_checkpoint_signature = signature_before_load
            if checkpoint_state == "corrupt" and len(tasks_df) > 0:
                # Replace the damaged file with a valid (empty) checkpoint up
                # front so workers merge into something readable and the damage
                # does not linger on disk if every task in this run fails.
                try:
                    self._write_checkpoint_locked()
                except Exception as e:
                    logger.warning(f"Could not rewrite the corrupted checkpoint file: {e}")

        settlements_by_pos: Dict[int, Dict[str, Any]] = {}
        pending: List[Tuple[int, Any]] = []
        seen_in_manifest = set()

        for pos, (_, task) in enumerate(tasks_df.iterrows()):
            task_id = self._task_id_of(task)
            if not task_id:
                settlements_by_pos[pos] = self._build_settlement(
                    task,
                    STATUS_FAILED,
                    "Task is missing a 'task_id'; it was not submitted or checkpointed.",
                    task=task,
                )
            elif task_id in completed_ids:
                settlements_by_pos[pos] = self._build_settlement(
                    task,
                    STATUS_SKIPPED,
                    "Already completed in checkpoint; skipped.",
                    attempts=0,
                    task=task,
                )
            elif task_id in seen_in_manifest:
                settlements_by_pos[pos] = self._build_settlement(
                    task,
                    STATUS_SKIPPED,
                    "Duplicate task_id in manifest; only the first occurrence was submitted.",
                    attempts=0,
                    task=task,
                )
            else:
                seen_in_manifest.add(task_id)
                pending.append((pos, task))

        pre_settled_skipped = sum(
            1 for s in settlements_by_pos.values() if s.get("status") == STATUS_SKIPPED
        )
        logger.info(
            f"Found {len(pending)} pending tasks out of {len(tasks_df)} total "
            f"({pre_settled_skipped} skipped before submission)."
        )

        successes: List[Dict[str, Any]] = []
        if pending:
            # Concurrency is always governed by the default domain's setting.
            default_config = self._get_domain_config('default')
            max_workers = max(1, int(default_config.concurrency)) if default_config else 2

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_pos = {
                    executor.submit(self._fetch_and_save_task, task): pos
                    for pos, task in pending
                }
                logger.info(
                    f"Submitted {len(future_to_pos)} tasks to thread pool with {max_workers} workers."
                )

                for future in as_completed(future_to_pos):
                    pos = future_to_pos[future]
                    task = tasks_df.iloc[pos]
                    try:
                        settlement = future.result()
                    except Exception as exc:  # defensive: worker should never leak
                        logger.error(
                            f"  - Task ID '{self._task_id_of(task)[:10]}' generated an unhandled exception: {exc}",
                            exc_info=True,
                        )
                        settlement = self._build_settlement(
                            task,
                            STATUS_FAILED,
                            f"Unhandled worker error: {type(exc).__name__}: {exc}",
                            task=task,
                        )

                    settlements_by_pos[pos] = settlement
                    if settlement.get("status") == STATUS_SUCCESS:
                        successes.append(
                            {
                                "task": settlement.get("task", task),
                                "raw_file_path": settlement["raw_file_path"],
                            }
                        )

        records = [settlements_by_pos[pos] for pos in range(len(tasks_df))]
        report = FetchReport(records=records, successes=successes)

        summary = report.summary
        logger.info(
            f"Completed fetching. Settled {summary['total']} task(s): "
            f"{summary[STATUS_SUCCESS]} succeeded, {summary[STATUS_FAILED]} failed, "
            f"{summary[STATUS_SKIPPED]} skipped."
        )
        for record in report.failed_records:
            logger.error(
                f"  - FAILED {record['task_id'][:10]} "
                f"[{record['interface_id']} @ {record['source_domain']}]: {record['reason']}"
            )
        return report
