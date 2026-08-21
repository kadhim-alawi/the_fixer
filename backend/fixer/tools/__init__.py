"""The complete tool surface the agent is given.

Importing the submodules is what registers them, so the registry is populated
as a side effect of importing this package.
"""

from __future__ import annotations

from . import act, read, reason, verify  # noqa: F401  (imported for registration)
from .base import (
    FUNCTIONS,
    REGISTRY,
    ToolCall,
    ToolEnv,
    ToolMeta,
    get_env,
    set_env,
)

READ_TOOLS = [
    read.survey_segments,
    read.query_conversion_funnel,
    read.query_payments,
    read.query_orders,
    read.query_logs,
    read.query_deployments,
    read.query_configuration,
    read.query_feature_flags,
    read.query_support_tickets,
    read.query_infrastructure,
]

ACT_TOOLS = [
    act.rollback_deployment,
    act.update_configuration,
    act.restore_configuration,
    act.disable_feature,
    act.restart_service,
    act.issue_goodwill_refunds,
]

VERIFY_TOOLS = [
    verify.check_conversion,
    verify.check_payment_success,
    verify.check_error_rate,
    verify.wait_for_traffic,
]

REASON_TOOLS = [
    reason.record_finding,
    reason.record_hypothesis,
    reason.revise_hypothesis,
    reason.assess_remediation,
    reason.conclude_mission,
]

ALL_TOOLS = READ_TOOLS + ACT_TOOLS + VERIFY_TOOLS + REASON_TOOLS

__all__ = [
    "ALL_TOOLS",
    "READ_TOOLS",
    "ACT_TOOLS",
    "VERIFY_TOOLS",
    "REASON_TOOLS",
    "REGISTRY",
    "FUNCTIONS",
    "ToolCall",
    "ToolEnv",
    "ToolMeta",
    "get_env",
    "set_env",
]
