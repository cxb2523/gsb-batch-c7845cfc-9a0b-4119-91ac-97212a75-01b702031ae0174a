"""The OrbitPilot step executor.

Runs a list of declarative steps against an injected page adapter and
produces a deterministic, human-readable manifest.

Semantics
---------
* Every step is recorded with ``name``, ``action``, ``status``
  (``success`` / ``skipped`` / ``failed``) and a ``reason``.
* A failing step never aborts the run — later steps still execute.
* A step whose ``condition`` text does not match is *skipped*, not failed.
* A step whose ``when`` expression is false is *skipped*, and the action
  is never invoked. A ``when`` that references a variable nothing has
  stored yet is also *skipped* (with the reason recorded), never a crash.
* ``read_text`` stores the element text under ``store_as`` so later
  ``when`` expressions can reference it.
* The manifest marks the run ``complete`` only when no step failed;
  skipped steps do not affect completeness. Re-running the same
  configuration yields a structurally identical manifest.
"""

from __future__ import annotations

from .adapters import PageAdapter, PageError
from .expressions import InvalidExpression, UndefinedVariable, evaluate

STATUS_SUCCESS = "success"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

_KNOWN_ACTIONS = ("click", "read_text", "navigate")


def run_strategy(config, adapter):
    """Execute ``config["strategy"]`` against ``adapter``.

    Returns a manifest dict: ``{"steps": [...], "complete": bool,
    "summary": {...}}``. The manifest is JSON-serializable and
    deterministic for a given (config, adapter) pair.
    """
    if not isinstance(adapter, PageAdapter):
        raise TypeError("adapter must implement the PageAdapter interface")
    steps = (config or {}).get("strategy") or []
    store = {}
    records = []
    for index, step in enumerate(steps):
        records.append(_run_step(step or {}, index, adapter, store))
    failed = sum(1 for r in records if r["status"] == STATUS_FAILED)
    skipped = sum(1 for r in records if r["status"] == STATUS_SKIPPED)
    succeeded = sum(1 for r in records if r["status"] == STATUS_SUCCESS)
    return {
        "steps": records,
        "complete": failed == 0,
        "summary": {
            "total": len(records),
            "success": succeeded,
            "skipped": skipped,
            "failed": failed,
        },
    }


def render_manifest(manifest):
    """Render a manifest as a human-readable, deterministic text listing."""
    lines = ["OrbitPilot run manifest", "=" * 24]
    for index, record in enumerate(manifest["steps"], start=1):
        line = "[%d] %s (%s): %s" % (
            index,
            record["name"],
            record["action"],
            record["status"].upper(),
        )
        if record["reason"]:
            line += " — %s" % record["reason"]
        lines.append(line)
    summary = manifest["summary"]
    verdict = "COMPLETE" if manifest["complete"] else "INCOMPLETE"
    lines.append(
        "Overall: %s (%d succeeded, %d skipped, %d failed)"
        % (verdict, summary["success"], summary["skipped"], summary["failed"])
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _run_step(step, index, adapter, store):
    name = step.get("name") or "step_%d" % (index + 1)
    action = step.get("action")
    record = {"name": name, "action": action, "status": None, "reason": ""}

    def finish(status, reason=""):
        record["status"] = status
        record["reason"] = reason
        return record

    if action not in _KNOWN_ACTIONS:
        return finish(STATUS_FAILED, "unknown action: %r" % (action,))

    # 1. `when` gate — evaluated before anything touches the page.
    when = step.get("when")
    if when is not None:
        try:
            allowed = evaluate(when, store)
        except UndefinedVariable as exc:
            return finish(
                STATUS_SKIPPED,
                "when references variable %r, which no earlier step stored"
                % (exc,),
            )
        except InvalidExpression as exc:
            return finish(STATUS_FAILED, "invalid when expression: %s" % (exc,))
        if not allowed:
            return finish(STATUS_SKIPPED, "when expression is false: %r" % (when,))

    # 2. `condition` gate — a text mismatch means skip, never fail.
    condition = step.get("condition")
    if condition:
        skipped = _check_condition(condition, step, adapter)
        if skipped is not None:
            return finish(STATUS_SKIPPED, skipped)

    # 3. The action itself.
    try:
        if action == "click":
            adapter.click(step.get("selector"))
        elif action == "read_text":
            text = adapter.read_text(step.get("selector"))
            record["value"] = text
            store_as = step.get("store_as")
            if store_as:
                store[store_as] = text
        elif action == "navigate":
            adapter.navigate(step.get("url"))
    except PageError as exc:
        return finish(STATUS_FAILED, str(exc))

    return finish(STATUS_SUCCESS)


def _check_condition(condition, step, adapter):
    """Return a skip reason if the condition is unmet, else ``None``."""
    ctype = condition.get("type")
    if ctype != "text_match":
        # Unknown condition types cannot be verified; treat as unmet so
        # the step is skipped rather than mistakenly executed.
        return "unknown condition type: %r" % (ctype,)
    selector = condition.get("selector") or step.get("selector")
    expected = str(condition.get("value", ""))
    try:
        actual = adapter.read_text(selector)
    except PageError as exc:
        return "condition target unreadable: %s" % (exc,)
    if expected not in actual:
        return "condition text %r not found in %r" % (expected, actual)
    return None
