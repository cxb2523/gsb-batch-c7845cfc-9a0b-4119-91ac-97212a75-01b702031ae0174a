"""Tool implementation for collect_instances_by_status."""

from functools import partial

from anyio import to_thread

from fastmcp.exceptions import ToolError

import json
from datetime import datetime
from .. import user_connection
from ..connection import CrossProjectReadError
from ..functions import collect_instances_by_status as _collect_instances_by_status
from ..services.compute import NOVA_SERVER_STATUSES, normalize_instance_statuses
from ..mcp_main import (
    logger,
    mcp,
)


@mcp.tool()
async def collect_instances_by_status(
    statuses: list[str], all_projects: bool = False
) -> str:
    """
    Collect instances for several statuses in one call, settling each status
    independently so one failed query never discards the others.

    Args:
        statuses: Nova server statuses to collect, case-insensitive (ACTIVE,
            BUILD, DELETED, ERROR, HARD_REBOOT, MIGRATING, PASSWORD, PAUSED,
            REBOOT, REBUILD, RESCUE, RESIZE, REVERT_RESIZE, SHELVED,
            SHELVED_OFFLOADED, SHUTOFF, SOFT_DELETED, SUSPENDED, UNKNOWN,
            VERIFY_RESIZE)
        all_projects: If True, include every project the credential's OpenStack
            policy allows. Requires ALLOW_CROSS_PROJECT_READ=true on the
            server; a disabled or denied request is reported as an error,
            never as current-project results.

    Returns:
        JSON with per-status settlement (queries: success or a safe error
        type, never credentials or response bodies), the combined instances,
        success/failure counts, and a scope description when all_projects is
        set. An invalid status or a failed cross-project read raises a
        ToolError (an MCP error result).
    """
    try:
        normalized = normalize_instance_statuses(statuses)
    except ValueError as error:
        raise ToolError(str(error)) from None

    # Multi-user mode must run inside an authenticated request identity;
    # never fall back to environment credentials or the global cache.
    if user_connection.enabled:
        try:
            user_connection.get_connection()
        except RuntimeError as error:
            raise ToolError(str(error)) from None

    try:
        logger.info(f"Collecting instances for statuses: {normalized}")

        result = await to_thread.run_sync(partial(
            _collect_instances_by_status, normalized, all_projects=all_projects))

        payload = {
            "timestamp": datetime.now().isoformat(),
            "statuses_requested": normalized,
            "all_projects": all_projects,
            **result,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    except CrossProjectReadError as error:
        raise ToolError(
            f"OpenStack query failed: collect_instances_by_status - {error}"
        ) from None
    except Exception as error:
        logger.error(
            "collect_instances_by_status failed error_type=%s",
            type(error).__name__)
        raise ToolError(
            "OpenStack query failed: collect_instances_by_status"
        ) from None
