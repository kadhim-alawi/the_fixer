"""Remediation tools.

Two rules hold across every tool here.

**A reason is mandatory.** The agent must state why it is doing something
before it does it. That text is what the approval dialog shows a human and
what the mission timeline records -- an action with no stated intent is not
reviewable.

**Success here is not success.** These return whether the *action* was carried
out. Whether the *problem* is fixed is a separate question, answered only by
the verification tools reading real metrics afterwards.
"""

from __future__ import annotations

from ..sim.incidents import Action
from .base import get_env, invoke, tool

_NOT_A_VERDICT = (
    "This reports only that the action was carried out. It is not evidence "
    "that the original problem is resolved -- verify against metrics."
)


@tool(kind="act", permission="EXECUTE", risk="MEDIUM", reversibility="REVERSIBLE")
async def rollback_deployment(deployment_ref: str, reason: str) -> dict:
    """Roll a deployment back to the previously deployed version.

    Reverts application code only. Runtime configuration is versioned
    separately and is not affected by a rollback, including configuration that
    the deployment's own release script wrote.

    Args:
        deployment_ref: The deployment reference, for example "8472".
        reason: Why this rollback is expected to help.

    Returns:
        Whether the rollback was carried out, and any side effects.
    """

    async def body() -> dict:
        res = await get_env().world.apply_action(
            Action("rollback_deployment", deployment_ref, {"reason": reason})
        )
        return {**res, "caveat": _NOT_A_VERDICT}

    return await invoke(
        "rollback_deployment", {"deployment_ref": deployment_ref, "reason": reason}, body
    )


@tool(kind="act", permission="WRITE", risk="MEDIUM", reversibility="REVERSIBLE")
async def update_configuration(key: str, value: str, reason: str) -> dict:
    """Set a runtime configuration key to a specific value.

    Takes effect immediately for traffic served after the change.

    Args:
        key: The configuration key, for example "payments.ios.provider_profile".
        value: The value to set.
        reason: Why this value is expected to resolve the problem.

    Returns:
        Whether the change was applied, and the before and after values.
    """

    async def body() -> dict:
        res = await get_env().world.apply_action(
            Action("update_configuration", key, {"value": value, "reason": reason})
        )
        return {**res, "caveat": _NOT_A_VERDICT}

    return await invoke(
        "update_configuration", {"key": key, "value": value, "reason": reason}, body
    )


@tool(kind="act", permission="WRITE", risk="LOW", reversibility="REVERSIBLE")
async def restore_configuration(key: str, reason: str) -> dict:
    """Restore a configuration key to the value it held before its last change.

    Use this when a key was changed recently and the previous value is known to
    have been working.

    Args:
        key: The configuration key to restore.
        reason: Why restoring the previous value is expected to help.

    Returns:
        Whether the restore was applied, and the before and after values.
    """

    async def body() -> dict:
        res = await get_env().world.apply_action(
            Action("restore_configuration", key, {"reason": reason})
        )
        return {**res, "caveat": _NOT_A_VERDICT}

    return await invoke("restore_configuration", {"key": key, "reason": reason}, body)


@tool(kind="act", permission="WRITE", risk="LOW", reversibility="REVERSIBLE")
async def disable_feature(flag_key: str, reason: str) -> dict:
    """Turn a feature flag off for all traffic.

    Args:
        flag_key: The flag to disable, for example "checkout.express_wallet".
        reason: Why disabling this feature is expected to help.

    Returns:
        Whether the flag was disabled.
    """

    async def body() -> dict:
        res = await get_env().world.apply_action(
            Action("disable_feature", flag_key, {"reason": reason})
        )
        return {**res, "caveat": _NOT_A_VERDICT}

    return await invoke("disable_feature", {"flag_key": flag_key, "reason": reason}, body)


@tool(kind="act", permission="EXECUTE", risk="MEDIUM", reversibility="REVERSIBLE")
async def restart_service(service: str, reason: str) -> dict:
    """Restart a service's processes.

    Clears in-memory state and reloads configuration from its current values.
    It does not change what those values are.

    Args:
        service: One of checkout-svc, payments-svc, search-svc, catalog-svc, web-edge.
        reason: Why a restart is expected to help.

    Returns:
        Whether the restart was carried out.
    """

    async def body() -> dict:
        res = await get_env().world.apply_action(
            Action("restart_service", service, {"reason": reason})
        )
        return {**res, "caveat": _NOT_A_VERDICT}

    return await invoke("restart_service", {"service": service, "reason": reason}, body)


@tool(kind="act", permission="EXECUTE", risk="HIGH", reversibility="IRREVERSIBLE")
async def issue_goodwill_refunds(since_minutes: int, reason: str) -> dict:
    """Refund every failed order in a recent window as a goodwill gesture.

    Moves real money and cannot be undone. This exists as a customer-recovery
    step after an incident is already resolved; it never fixes a cause.

    Args:
        since_minutes: Refund failed orders from this many minutes ago until now.
        reason: Why these refunds are warranted.

    Returns:
        The number of orders refunded and the total value.
    """

    async def body() -> dict:
        res = await get_env().world.apply_action(
            Action(
                "issue_goodwill_refunds",
                str(since_minutes),
                {"since_minutes": since_minutes, "reason": reason},
            )
        )
        return {**res, "caveat": _NOT_A_VERDICT}

    return await invoke(
        "issue_goodwill_refunds",
        {"since_minutes": since_minutes, "reason": reason},
        body,
    )
