"""gevent keywords"""

import time
from contextlib import contextmanager
from collections import OrderedDict
from copy import copy
from enum import Enum
from typing import Any, Dict, List, Optional
from typing import OrderedDict as od
from uuid import uuid4

from gevent import Timeout, joinall, killall, spawn, pool
from robot.api.deco import keyword
from robot.libraries.BuiltIn import BuiltIn
from robot.running.context import EXECUTION_CONTEXTS
from robot.utils import safe_str

from GeventLibrary.exceptions import (
    AliasAlreadyCreated,
    BundleHasNoCoroutines,
    NoBundleCreated,
)


class CoroutineStatus(str, Enum):
    """Execution status of a single coroutine.

    The three possible states are:

    - ``COMPLETED``: the coroutine finished successfully and has a value.
    - ``EXCEPTION``: the coroutine finished by raising an exception.
    - ``TIMEOUT``: the coroutine did not finish before the timeout and was killed.
    """

    COMPLETED = "completed"
    EXCEPTION = "exception"
    TIMEOUT = "timeout"

    # aliases, for readability at the call site
    SUCCESS = "completed"
    FAILED = "exception"
    TIMED_OUT = "timeout"

    def __str__(self) -> str:
        return self.value


class CoroutineResult(dict):
    """Outcome of a single coroutine when ``Run Coroutines`` runs in tolerant mode.

    The result behaves like a dictionary, so in robot files the fields can be
    read with item access, e.g. ``${result}[status]`` or ``${result}[value]``.
    The same fields are also exposed as attributes for python consumers,
    e.g. ``result.status`` or ``result.exception_message``.

    Fields:

    - ``keyword_name``: name of the keyword that was added as a coroutine.
    - ``status``: one of ``completed``, ``exception`` or ``timeout``.
    - ``value``: the returned value for completed coroutines, otherwise ``None``.
    - ``exception``: the raised exception for failed coroutines, otherwise ``None``.
    - ``exception_type``: exception class name (e.g. ``ValueError``), or ``None``.
    - ``exception_message``: exception message, or ``None``.
    - ``reason``: a human readable failure reason (``<type>: <message>``), or ``None``.
    - ``successful`` / ``failed`` / ``timed_out``: boolean shortcuts.

    ``keyword``, ``name``, ``error``, ``failure_reason``, ``error_type``,
    ``error_message`` and ``message`` are available as aliases, while
    ``state``, ``is_completed``, ``is_exception`` and ``is_timeout`` are
    read-only shortcuts of the status.
    """

    COMPLETED = CoroutineStatus.COMPLETED
    EXCEPTION = CoroutineStatus.EXCEPTION
    TIMEOUT = CoroutineStatus.TIMEOUT
    FAILED = CoroutineStatus.EXCEPTION

    _ALIASES = {
        "keyword": "keyword_name",
        "name": "keyword_name",
        "error": "exception",
        "failure_reason": "reason",
        "error_type": "exception_type",
        "error_message": "exception_message",
        "message": "exception_message",
    }

    def __init__(
        self,
        keyword_name: Optional[str] = None,
        status: CoroutineStatus = CoroutineStatus.COMPLETED,
        value: Any = None,
        exception: Optional[BaseException] = None,
    ) -> None:
        status = CoroutineStatus(status)
        exception_type = type(exception).__name__ if exception is not None else None
        exception_message = str(exception) if exception is not None else None
        if status is CoroutineStatus.EXCEPTION:
            reason = f"{exception_type}: {exception_message}"
        elif status is CoroutineStatus.TIMEOUT:
            reason = "timeout: coroutine did not finish before the timeout"
        else:
            reason = None
        super().__init__(
            keyword_name=keyword_name,
            status=status.value,
            value=value,
            exception=exception,
            exception_type=exception_type,
            exception_message=exception_message,
            reason=reason,
            successful=status is CoroutineStatus.COMPLETED,
            failed=status is not CoroutineStatus.COMPLETED,
            timed_out=status is CoroutineStatus.TIMEOUT,
        )

    def __getattr__(self, name: str) -> Any:
        # only called when the regular attribute lookup fails,
        # so dictionary methods keep working as usual.
        try:
            return self[self._resolve(name)]
        except KeyError as ex:
            raise AttributeError(name) from ex

    def __setattr__(self, name: str, value: Any) -> None:
        key = self._resolve(name)
        if key in dict.keys(self):
            dict.__setitem__(self, key, value)
        else:
            super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        key = self._resolve(name)
        if key in dict.keys(self):
            dict.__delitem__(self, key)
        else:
            super().__delattr__(name)

    @classmethod
    def _resolve(cls, key: str) -> str:
        """resolves a canonical field name or one of its aliases"""
        return cls._ALIASES.get(key, key)

    def __getitem__(self, key: str) -> Any:
        return dict.__getitem__(self, self._resolve(key))

    def __setitem__(self, key: str, value: Any) -> None:
        dict.__setitem__(self, self._resolve(key), value)

    def __delitem__(self, key: str) -> None:
        dict.__delitem__(self, self._resolve(key))

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            key = self._resolve(key)
        return dict.__contains__(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        """dictionary style access that also resolves field aliases"""
        return dict.get(self, self._resolve(key), default)

    @property
    def status_enum(self) -> CoroutineStatus:
        """status as a `CoroutineStatus` enum member"""
        return CoroutineStatus(self["status"])

    @property
    def state(self) -> str:
        """alias of `status`"""
        return self["status"]

    @property
    def is_completed(self) -> bool:
        """whether the coroutine completed successfully"""
        return self["status"] == CoroutineStatus.COMPLETED.value

    @property
    def is_exception(self) -> bool:
        """whether the coroutine finished by raising an exception"""
        return self["status"] == CoroutineStatus.EXCEPTION.value

    @property
    def is_timeout(self) -> bool:
        """whether the coroutine was killed because of the timeout"""
        return self["status"] == CoroutineStatus.TIMEOUT.value

    def to_dict(self) -> Dict[str, Any]:
        """return the result as a plain dictionary"""
        return dict(self)

    as_dict = to_dict

    def __str__(self) -> str:
        if self["status"] == CoroutineStatus.EXCEPTION.value:
            return f"{self['keyword_name']}: exception ({self['reason']})"
        if self["status"] == CoroutineStatus.TIMEOUT.value:
            return f"{self['keyword_name']}: timeout"
        return f"{self['keyword_name']}: completed ({self['value']!r})"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(keyword_name={self['keyword_name']!r}, "
            f"status={self['status']!r}, value={self['value']!r}, "
            f"exception={self['exception']!r})"
        )


class RobotKeywordCoroutine:
    """Class defining a keywords for coroutine"""

    def __init__(self, keyword_name, *args, **kwargs) -> None:
        self._keyword_name = keyword_name
        self._args = args
        self._kwargs = kwargs

    @property
    def keyword_name(self):
        """keyword to execute"""
        return self._keyword_name

    @property
    def all_args(self):
        """args and kwargs in robotframework format"""
        return [
            *self._args,
            *[f"{key}={value}" for key, value in self._kwargs.items()],
        ]


@contextmanager
def monkey_patch_robot_ctx():
    """provides a context manager for safe and succinct coroutine execution context"""
    built_in = BuiltIn()

    ctx = EXECUTION_CONTEXTS.current
    ## monkey
    start_keyword_placer = copy(ctx.output.start_keyword)
    end_keyword_placer = copy(ctx.output.end_keyword)

    ctx.output.start_keyword = lambda kw: _start_keyword(kw, built_in)
    ctx.output.end_keyword = lambda kw: _end_keyword(kw, built_in)
    ## end monkey
    try:
        yield built_in
    finally:
        # unmonkey
        ctx.output.start_keyword = start_keyword_placer
        ctx.output.end_keyword = end_keyword_placer
    ## end unmonkey


def _start_keyword(keyword_item, built_in):
    """listener for starting a keyword - drop it as HTML table"""
    html_text = f"""
            <style>
                #demo table, #demo th, #demo td{{
                    border: 1px dotted black;
                    border-collapse: collapse;
                    table-layout: auto;
                }}
            </style>
            <table id="demo" style="width:100%">
                <tr>
                    <th style="width:10%">Event</th>
                    <th style="width:10%">Keyword</th>
                    <th style="width:10%">Args</th>
                    <th style="width:10%">Doc</th>
                </tr>
                <tr>
                    <td style="text-align:center">Started</td>
                    <td style="text-align:center">{keyword_item.name}</td>
                    <td style="text-align:center">{"   ".join([safe_str(a) for a in keyword_item.args])}</td>
                    <td style="text-align:center">{keyword_item.doc}</td>
                </tr>
            </table>

            """
    built_in.log(html_text, html=True)


def _end_keyword(keyword_item, built_in):
    """listener for ending a keyword - drop it as HTML table"""
    html_text = f"""
            <style>
                #demo table, #demo th, #demo td{{
                    border: 1px dotted black;
                    border-collapse: collapse;
                    table-layout: auto;
                }}
                #statusfail{{
                    border: 1px dotted black;
                    color:red;
                    bgcolor:gray;
                    text-align:center;
                    border-collapse: collapse;
                    table-layout: auto;
                    }}
                #statuspass{{
                    border: 1px dotted black;
                    color:green;
                    bgcolor:gray;
                    text-align:center;
                    border-collapse: collapse;
                    table-layout: auto;
                    }}
            </style>
            <table id="demo" style="width:100%">
                <tr>
                    <th style="width:10%">Event</th>
                    <th style="width:10%">Keyword</th>
                    <th style="width:10%">Args</th>
                    <th style="width:10%">Doc</th>
                    <th style="width:10%">Status</th>
                </tr>
                <tr>
                    <td style="text-align:center">Completed</td>
                    <td style="text-align:center">{keyword_item.name}</td>
                    <td style="text-align:center">{"   ".join([safe_str(a) for a in keyword_item.args])}</td>
                    <td style="text-align:center">{keyword_item.doc}</td>
                    <td id="status{keyword_item.result.status.lower()}">{keyword_item.result.status}</td>
                </tr>
            </table>

            """
    built_in.log(html_text, html=True)


class GeventKeywords:
    """class defining gevent keywords"""

    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    def __init__(self) -> None:
        self._active_gevent_bundles: od[str, List[RobotKeywordCoroutine]] = (
            OrderedDict()
        )

    @keyword
    def create_gevent_bundle(self, alias: str = None):
        """this methods creates a bundle for coroutines to run,
        once the bundle is created you can attach keywords to it
        these keywords will be executed asynchronously when `Run Coroutines` is called
        Examples:

        |     Create Gevent Bundle
        |     Create Gevent Bundle    alias=alias1

        Args:

            ``alias`` <str, optional> Name of alias. Defaults to None.

        """
        alias = alias or str(uuid4())
        if alias in self._active_gevent_bundles:
            raise AliasAlreadyCreated(
                f"An alias with name {alias} has already been created."
            )
        self._active_gevent_bundles[alias] = []

    @keyword
    def add_coroutine(
        self,
        keyword_name: str,
        *args,
        alias: str = None,
        **kwargs,
    ):
        """Adding a new keyword to be a coroutine of the bundle,
        If no bundle alias is given, the last created bundle will be used by default
        Examples:

        |       Add Coroutine    Sleep    1s
        |       Add Coroutine    Sleep    1s    alias=alias1
        |       Add Coroutine    Convert To Lower Case    UPPER
        Args:

            ``keyword_name``            <str> Explicit robotframework keyword name

            ``*args``                   <args> all positional arguments of the keywords.

            ``alias``                   <str, optional> Name of alias. Defaults to None.

            ``**kwargs``                <kwargs> all keyword arguments of the keywords
        """
        self[alias].append(RobotKeywordCoroutine(keyword_name, *args, **kwargs))

    @keyword
    def run_coroutines(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        self,
        alias: str = None,
        timeout: int = 200,
        gevent_pool_size: int = 0,
        raise_on_failure: bool = True,
        continue_on_failure: Optional[bool] = None,
        **failure_options,
    ) -> List:
        """Runs all the coroutines asynchronously.

        Args:

            ``alias``               <str, optional> Name of alias. Defaults to None.

            ``timeout``             <int, optional> Coroutines execution timeout in seconds. Defaults to 200.

            ``gevent_pool_size``    <int> Size of gevent pool, 0 for using spawn without pooling. Defaults to 0.

            ``raise_on_failure``    <bool, optional> Failure strategy. When True (the default)
                                    the first exception raised by a coroutine is re-raised,
                                    preserving the previous behaviour. When False the execution
                                    is fault tolerant: no exception is raised and a list of
                                    `CoroutineResult` objects is returned instead.
                                    Defaults to True.

            ``continue_on_failure`` <bool, optional> Inverted alias for ``raise_on_failure``,
                                    for readability in robot files. When given, it overrides
                                    ``raise_on_failure``. Defaults to None.

            ``**failure_options``   additional names accepted for the failure strategy:
                                    ``raise_on_error``, ``fail_fast``,
                                    ``stop_on_failure`` (as ``raise_on_failure``) and
                                    ``continue_on_error``, ``ignore_errors``,
                                    ``ignore_failures`` (as ``continue_on_failure``).


            |    ${values}    Run Coroutines    alias=alias1
            |    ${results}   Run Coroutines    alias=alias1    raise_on_failure=${False}
            |    ${results}   Run Coroutines    alias=alias1    continue_on_failure=${True}

        Returns:

            ``list`` <List[Any]>   when ``raise_on_failure`` is True - all returned values
            from coroutines by order.

            ``list`` <List[CoroutineResult]>   when ``raise_on_failure`` is False - one result
            object per coroutine, in the order the coroutines were added. Each result exposes
            a ``status`` of ``completed``, ``exception`` or ``timeout``, together with the
            returned ``value`` or the ``exception``/``exception_type``/``exception_message``
            of the failure.

        Coroutines that are still running when ``timeout`` expires are killed and reported
        with the ``timeout`` status. In every case - success, failure or timeout - the
        coroutines bundle is emptied at the end of the execution, so the same alias can be
        populated and executed again.
        """
        if gevent_pool_size < 0:
            raise ValueError(
                f"'gevent_pool_size' must be a non negative value, got {gevent_pool_size}"
            )
        raise_on_failure = self._resolve_failure_strategy(
            raise_on_failure, continue_on_failure, failure_options
        )

        coros = self[alias]
        if len(coros) == 0:
            raise BundleHasNoCoroutines(
                "The given bundle has no coroutines, please use `Add Coroutine` keyword"
            )
        # keep the added order; `None` marks a coroutine that never got spawned
        coros_snapshot = list(coros)
        jobs: List[Optional[Any]] = [None] * len(coros_snapshot)
        finished: List[Any] = []
        timed_out = set()
        deadline = None if timeout is None else time.monotonic() + timeout

        try:
            with monkey_patch_robot_ctx() as built_in:
                try:
                    # the timeout is a deadline for the whole execution, so a pool
                    # that has to wait for a free slot also counts against it
                    with Timeout(timeout, False):
                        spawn_callable = spawn
                        if gevent_pool_size > 0:
                            spawn_callable = pool.Pool(gevent_pool_size).spawn
                        for index, coro in enumerate(coros_snapshot):
                            jobs[index] = spawn_callable(
                                built_in.run_keyword,
                                coro.keyword_name,
                                *coro.all_args,
                            )

                    # waits for all the coroutines, or until the timeout expires.
                    # Finished greenlets are returned in the order they became ready,
                    # which is the order the previous implementation checked them in.
                    remaining = (
                        None
                        if deadline is None
                        else max(0.0, deadline - time.monotonic())
                    )
                    finished = joinall(
                        [job for job in jobs if job is not None], timeout=remaining
                    )
                finally:
                    # coroutines that are still running (timeout expired or the
                    # execution failed midway) must not keep writing in the background.
                    # Killing happens while the robot context is still patched so the
                    # terminated keywords are reported properly.
                    killed = self._terminate_greenlets(
                        job for job in jobs if job is not None
                    )
                    timed_out = {
                        index
                        for index, job in enumerate(jobs)
                        if job is None or job in killed
                    }
        finally:
            # the bundle must be reusable after every execution attempt
            coros.clear()

        if not raise_on_failure:
            return [
                self._build_coroutine_result(coro, job, index in timed_out)
                for index, (coro, job) in enumerate(zip(coros_snapshot, jobs))
            ]

        # preserving the previous fail-fast behaviour: the exception of the first
        # coroutine that finished in time with an error is re-raised, with its
        # original message
        for greenlet in finished:
            if greenlet.exception:
                raise greenlet.exception

        # `value` of a still running/killed greenlet is not a meaningful result,
        # keep returning `None` for coroutines that did not finish in time
        return [
            None if index in timed_out or job is None else job.value
            for index, job in enumerate(jobs)
        ]

    @staticmethod
    def _as_bool(value: Any, option: str) -> bool:
        """converts robot style booleans to python booleans"""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("false", "no", "off", "0", "none", ""):
                return False
            if normalized in ("true", "yes", "on", "1"):
                return True
            raise ValueError(f"'{option}' must be a boolean value, got {value!r}")
        return bool(value)

    @classmethod
    def _resolve_failure_strategy(
        cls,
        raise_on_failure: bool,
        continue_on_failure: Optional[bool],
        failure_options: Dict[str, Any],
    ) -> bool:
        """resolves the failure strategy out of the supported parameter names"""
        if continue_on_failure is not None:
            raise_on_failure = not cls._as_bool(
                continue_on_failure, "continue_on_failure"
            )
        raise_aliases = ("raise_on_error", "fail_fast", "stop_on_failure")
        continue_aliases = ("continue_on_error", "ignore_errors", "ignore_failures")
        for name, value in failure_options.items():
            if name in raise_aliases:
                raise_on_failure = cls._as_bool(value, name)
            elif name in continue_aliases:
                raise_on_failure = not cls._as_bool(value, name)
            else:
                raise TypeError(
                    f"Run Coroutines got an unexpected keyword argument '{name}'"
                )
        return cls._as_bool(raise_on_failure, "raise_on_failure")

    @staticmethod
    def _terminate_greenlets(greenlets) -> List:
        """kills the still running greenlets, waits for them to actually stop
        and returns the list of greenlets that were (or already are) dead"""
        alive = [greenlet for greenlet in greenlets if not greenlet.ready()]
        if not alive:
            return []
        try:
            killall(alive, block=True, timeout=5)
        except Timeout:
            # a coroutine that ignores GreenletExit cannot be forced to stop,
            # make sure we never block the whole execution because of it
            killall(alive, block=False)
        return alive

    @staticmethod
    def _build_coroutine_result(
        coro: RobotKeywordCoroutine, job, timed_out: bool
    ) -> CoroutineResult:
        """translates a greenlet state into a `CoroutineResult`"""
        if timed_out or job is None:
            return CoroutineResult(
                keyword_name=coro.keyword_name,
                status=CoroutineStatus.TIMEOUT,
            )
        if job.exception is not None:
            return CoroutineResult(
                keyword_name=coro.keyword_name,
                status=CoroutineStatus.EXCEPTION,
                exception=job.exception,
            )
        return CoroutineResult(
            keyword_name=coro.keyword_name,
            status=CoroutineStatus.COMPLETED,
            value=job.value,
        )

    @keyword
    def clear_bundle(self, alias: str = None):
        """
        removes a single coroutines bundle from the list

        Args:
            ``alias``               <str, optional> Name of alias. Defaults to None.
        """
        try:
            alias = alias or list(self._active_gevent_bundles.items())[-1][0]
            self._active_gevent_bundles.pop(alias).clear()
        except KeyError as ex:
            raise LookupError(f"Bundle with alias {alias} was not found") from ex

    @keyword
    def clear_all_bundles(self):
        """
        removes all coroutines bundles from the list
        """
        self._active_gevent_bundles.clear()

    def __len__(self):
        return len(self._active_gevent_bundles)

    def __getitem__(self, alias: Optional[str] = None) -> List[RobotKeywordCoroutine]:
        if len(self._active_gevent_bundles) == 0:
            raise NoBundleCreated(
                "Please create a bundle with `Create Gevent Bundle` keyword"
            )

        if alias:
            if alias not in self._active_gevent_bundles:
                raise LookupError(f"Bundle with alias {alias} was not found")
            return self._active_gevent_bundles[alias]
        return list(self._active_gevent_bundles.items())[-1][1]
