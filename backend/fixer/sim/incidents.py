"""Incident definitions.

An incident is a *cause*, not a script. It plants causal artifacts in the world
(a config change, a deployment, a flag flip), declares the effects those
artifacts have on generated traffic, and declares which remediation actually
removes the cause.

Nothing here tells the agent anything. The agent only ever sees the downstream
data through tools. Recovery in the demo is a consequence of the agent being
right, not of a scripted step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

# ---------------------------------------------------------------------------
# Actions the agent can take against the world
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    kind: str  # rollback_deployment | update_configuration | ...
    target: str  # deployment ref, config key, flag key, service name
    params: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Effects: how an active cause distorts generated traffic
# ---------------------------------------------------------------------------


@dataclass
class Effect:
    """Applied to every generated session whose attributes match ``scope``.

    An empty scope matches everything.
    """

    scope: dict[str, str] = field(default_factory=dict)

    # Fraction of checkout_started sessions that additionally fail to convert.
    checkout_failure_boost: float = 0.0
    # Fraction of added_to_cart sessions that additionally abandon before checkout.
    cart_abandon_boost: float = 0.0
    # Error code stamped on the resulting failed payments.
    payment_error_code: str | None = None
    # Which funnel stage the failure is attributed to.
    abandon_stage: str = "payment"

    # Infrastructure distortion
    service: str | None = None
    service_error_rate: float | None = None
    latency_multiplier: float = 1.0

    # Log signature emitted while active
    log_service: str | None = None
    log_message: str | None = None
    logs_per_minute: int = 0

    def matches(self, **attrs: str) -> bool:
        return all(attrs.get(k) == v for k, v in self.scope.items())


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------


@dataclass
class Incident:
    key: str
    # Internal label for evaluation and post-hoc scoring. Never exposed to the agent.
    root_cause_label: str
    # Causal artifacts written into the world when the incident starts.
    setup: Callable[["WorldWriter", datetime], None]
    effects: list[Effect]
    # Does this action remove the cause?
    is_fixed_by: Callable[[Action], bool]
    # Plausible-but-wrong actions, recorded so evaluation can tell "agent was
    # fooled by a distractor" from "agent had no idea".
    known_decoys: list[str] = field(default_factory=list)
    # Extra noise planted alongside the real cause.
    distractors: list[Callable[["WorldWriter", datetime], None]] = field(
        default_factory=list
    )
    # Support-ticket text customers would plausibly file.
    ticket_templates: list[tuple[str, str, str | None]] = field(default_factory=list)


class WorldWriter:
    """Narrow interface the incident setup functions write through.

    Implemented by :class:`fixer.sim.world.World`; declared here to keep the
    incident definitions free of import cycles.
    """

    def set_config(
        self, key: str, value: str, ts: datetime, by: str, description: str = ""
    ) -> None: ...

    def add_deployment(
        self,
        ref: str,
        service: str,
        version: str,
        author: str,
        summary: str,
        ts: datetime,
    ) -> None: ...

    def set_flag(
        self,
        key: str,
        enabled: bool,
        ts: datetime,
        by: str,
        rollout_pct: int = 100,
        description: str = "",
    ) -> None: ...


# ---------------------------------------------------------------------------
# Incident A -- payment configuration regression (the flagship demo scenario)
# ---------------------------------------------------------------------------
#
# The story the data tells, if the agent digs far enough:
#
#   deployment #8472 ships                        <- correlates in time, is NOT the cause
#     ...37 minutes later...
#   its release script rewrites a runtime config  <- the actual cause
#     payments.ios.provider_profile: standard_v4 -> legacy_v2
#   legacy_v2 rejects tokenised iOS wallet payments with PAY_CFG_3021
#     -> iOS checkout completion collapses
#     -> overall conversion drops, web and android untouched
#
# Rolling back #8472 is a reasonable first hypothesis and a real, reversible
# action. It does not restore the config, so conversion does not recover. That
# failure is genuine: the verification query returns real numbers from real rows.


def _setup_incident_a(w: WorldWriter, t: datetime) -> None:
    w.add_deployment(
        ref="8472",
        service="checkout-svc",
        version="v4.19.0",
        author="m.okafor",
        summary="Checkout: migrate wallet tokenisation to shared payment profile loader",
        ts=t - timedelta(minutes=37),
    )
    w.set_config(
        key="payments.ios.provider_profile",
        value="legacy_v2",
        ts=t,
        by="release-script/8472",
        description="Provider profile used when tokenising iOS wallet payments.",
    )


def _distractor_a_traffic(w: WorldWriter, t: datetime) -> None:
    # A marketing campaign ended, so DE traffic really did fall. It has nothing
    # to do with conversion rate, but it shows up in any 'what changed?' sweep.
    w.set_config(
        key="marketing.campaign.de_summer.active",
        value="false",
        ts=t - timedelta(hours=2),
        by="c.rivera",
        description="Regional campaign toggle.",
    )


def _distractor_a_deploy(w: WorldWriter, t: datetime) -> None:
    w.add_deployment(
        ref="8471",
        service="search-svc",
        version="v2.8.3",
        author="l.haddad",
        summary="Search: bump ranking model, +40ms p95 expected",
        ts=t - timedelta(minutes=95),
    )


def _fixes_incident_a(a: Action) -> bool:
    if a.target != "payments.ios.provider_profile":
        return False
    if a.kind == "restore_configuration":
        return True
    if a.kind == "update_configuration":
        return a.params.get("value") == "standard_v4"
    return False


INCIDENT_A = Incident(
    key="payment_config_regression",
    root_cause_label="payments.ios.provider_profile set to legacy_v2 by release script 8472",
    setup=_setup_incident_a,
    effects=[
        Effect(
            scope={"platform": "ios"},
            checkout_failure_boost=0.80,
            payment_error_code="PAY_CFG_3021",
            abandon_stage="payment",
            log_service="payments-svc",
            log_message=(
                "provider profile 'legacy_v2' rejected tokenised wallet payment "
                "(PAY_CFG_3021: unsupported token format)"
            ),
            logs_per_minute=14,
        ),
        # The search deploy really did add latency. Real, visible, irrelevant.
        Effect(
            scope={},
            service="search-svc",
            latency_multiplier=1.45,
        ),
    ],
    is_fixed_by=_fixes_incident_a,
    known_decoys=["rollback_deployment:8472", "restart_service:checkout-svc"],
    distractors=[_distractor_a_traffic, _distractor_a_deploy],
    ticket_templates=[
        (
            "Can't complete purchase on iPhone",
            "I've tried three times to check out in the app on my iPhone. It spins and "
            "then says payment could not be processed. Card works fine elsewhere.",
            "ios",
        ),
        (
            "Payment declined but card is fine",
            "Tried Apple Pay and a saved card, both failed at the last step. Ordered the "
            "same thing on my laptop with no problem.",
            "ios",
        ),
        (
            "App checkout broken?",
            "Is checkout down for iOS? Been failing since this morning.",
            "ios",
        ),
    ],
)


REGISTRY: dict[str, Incident] = {
    INCIDENT_A.key: INCIDENT_A,
}


def get(key: str) -> Incident:
    return REGISTRY[key]
