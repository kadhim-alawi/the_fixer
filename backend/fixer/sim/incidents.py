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

    # Payment profile recorded on affected payment attempts.
    payment_profile: str | None = None

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
    # Tokens that must appear in a stated root cause for it to count as
    # correct. Keeps grading honest without demanding exact wording.
    root_cause_tokens: list[str] = field(default_factory=list)
    # Plausible-but-wrong actions, recorded so evaluation can tell "agent was
    # fooled by a distractor" from "agent had no idea".
    known_decoys: list[str] = field(default_factory=list)
    # Extra noise planted alongside the real cause.
    distractors: list[Callable[["WorldWriter", datetime], None]] = field(
        default_factory=list
    )
    # Support-ticket text customers would plausibly file.
    ticket_templates: list[tuple[str, str, str | None]] = field(default_factory=list)
    # Real, unrelated traffic changes: {region: fraction of sessions lost}.
    # These are genuine movements in the data that explain nothing.
    region_traffic_drop: dict[str, float] = field(default_factory=dict)
    # The canonical correct action, and the most plausible wrong one. Used to
    # validate the library mechanically -- applying the fix must recover the
    # metric, and applying the decoy must not. Never shown to the agent.
    reference_fix: "Action | None" = None
    reference_decoy: "Action | None" = None


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
    reference_fix=Action("restore_configuration", "payments.ios.provider_profile"),
    reference_decoy=Action("rollback_deployment", "8472"),
    root_cause_tokens=["payments.ios.provider_profile", "legacy_v2"],
    region_traffic_drop={"DE": 0.45},
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


# ---------------------------------------------------------------------------
# Incident B -- a genuinely bad deployment
# ---------------------------------------------------------------------------
#
# Deliberately the mirror image of Incident A. Here the deployment really is the
# cause and rolling it back really is the fix. Without a case like this, an
# agent could learn "rollbacks never work on NovaCart" and score well by
# pattern-matching the environment rather than by reasoning about it.


def _setup_b(w: WorldWriter, t: datetime) -> None:
    w.add_deployment(
        ref="8483",
        service="checkout-svc",
        version="v4.21.0",
        author="s.novak",
        summary="Checkout: refactor address serialisation for Android clients",
        ts=t,
    )


def _fixes_b(a: Action) -> bool:
    return a.kind == "rollback_deployment" and a.target == "8483"


INCIDENT_B = Incident(
    key="bad_deployment",
    root_cause_label="deployment 8483 broke checkout address serialisation on Android",
    setup=_setup_b,
    effects=[
        Effect(
            scope={"platform": "android"},
            checkout_failure_boost=0.78,
            payment_error_code="CHK_NULL_REF",
            abandon_stage="payment",
            service="checkout-svc",
            service_error_rate=0.07,
            log_service="checkout-svc",
            log_message=(
                "NullPointerException serialising delivery address for client "
                "'android' (CHK_NULL_REF at AddressCodec.write:212)"
            ),
            logs_per_minute=16,
        ),
        # The codec is shared. Android hits the broken path constantly; other
        # clients only on addresses with the same shape.
        Effect(
            scope={},
            checkout_failure_boost=0.16,
            payment_error_code="CHK_NULL_REF",
            abandon_stage="payment",
        ),
    ],
    is_fixed_by=_fixes_b,
    reference_fix=Action("rollback_deployment", "8483"),
    reference_decoy=Action("restart_service", "checkout-svc"),
    root_cause_tokens=["8483"],
    known_decoys=[
        "restore_configuration:checkout.address_validation",
        "restart_service:checkout-svc",
    ],
    ticket_templates=[
        ("App crashes at checkout", "Android app. Every time I tap pay it errors out.", "android"),
        ("Cannot place order",
         "Order fails at the last step on the Android app since this morning.", "android"),
    ],
)


# ---------------------------------------------------------------------------
# Incident C -- a feature flag turned on carelessly
# ---------------------------------------------------------------------------
#
# The only incident that produces no failed payments at all. Customers are lost
# at the shipping step, before they ever reach payment. An agent that goes
# straight to payment errors finds nothing; the funnel is the only thing that
# shows it.


def _setup_c(w: WorldWriter, t: datetime) -> None:
    w.set_flag(
        key="checkout.strict_address_match",
        enabled=True,
        ts=t,
        by="growth-experiments",
        rollout_pct=100,
        description="Reject orders whose delivery address does not exactly match the card address.",
    )


def _fixes_c(a: Action) -> bool:
    return a.kind == "disable_feature" and a.target == "checkout.strict_address_match"


INCIDENT_C = Incident(
    key="feature_flag_mistake",
    root_cause_label="checkout.strict_address_match enabled at 100% by growth-experiments",
    setup=_setup_c,
    effects=[
        Effect(
            scope={"platform": "web"},
            checkout_failure_boost=0.62,
            payment_error_code=None,
            abandon_stage="shipping",
            log_service="checkout-svc",
            log_message=(
                "strict address match rejected delivery address "
                "(billing/shipping mismatch); order not submitted"
            ),
            logs_per_minute=11,
        )
    ],
    is_fixed_by=_fixes_c,
    reference_fix=Action("disable_feature", "checkout.strict_address_match"),
    reference_decoy=Action("restore_configuration", "checkout.address_validation"),
    root_cause_tokens=["checkout.strict_address_match"],
    known_decoys=["rollback_deployment", "restore_configuration:checkout.address_validation"],
    ticket_templates=[
        ("Why won't it accept my address?",
         "It keeps saying my delivery address is invalid. It is the same address I always "
         "use, I just want it shipped to my office.", "web"),
        ("Checkout rejects delivery address",
         "Tried three times on the website. Won't let me ship anywhere other than my card address.",
         "web"),
    ],
)


# ---------------------------------------------------------------------------
# Incident D -- the payment provider is degraded, and it is not our fault
# ---------------------------------------------------------------------------
#
# Nothing was deployed and nothing was configured wrongly. Every "what changed
# on our side?" line of enquiry comes back empty, which is the point: the fix is
# to fail over to the documented backup provider, not to undo anything.


def _setup_d(w: WorldWriter, t: datetime) -> None:
    # No causal artifact on our side -- that absence is itself the evidence.
    return None


def _fixes_d(a: Action) -> bool:
    return (
        a.kind == "update_configuration"
        and a.target == "payments.provider.primary"
        and str(a.params.get("value", "")).lower() == "adyen"
    )


INCIDENT_D = Incident(
    key="provider_degradation",
    root_cause_label="primary payment provider stripe degraded; failover to adyen required",
    setup=_setup_d,
    effects=[
        Effect(
            scope={"platform": "web"},
            checkout_failure_boost=0.58,
            payment_error_code="PROVIDER_TIMEOUT",
            abandon_stage="payment",
            log_service="payments-svc",
            log_message=(
                "upstream provider 'stripe' did not respond within 8000ms "
                "(PROVIDER_TIMEOUT); no local error"
            ),
            logs_per_minute=13,
        ),
        Effect(
            scope={"platform": "android"},
            checkout_failure_boost=0.58,
            payment_error_code="PROVIDER_TIMEOUT",
            abandon_stage="payment",
        ),
    ],
    is_fixed_by=_fixes_d,
    reference_fix=Action("update_configuration", "payments.provider.primary", {"value": "adyen"}),
    reference_decoy=Action("restart_service", "payments-svc"),
    root_cause_tokens=["stripe"],
    known_decoys=["rollback_deployment", "restart_service:payments-svc"],
    ticket_templates=[
        ("Payment times out", "Card payment just spins and then fails. Tried twice.", "web"),
        ("Order won't go through", "Payment page hangs for ages then says try again later.", None),
    ],
)


# ---------------------------------------------------------------------------
# Incident E -- fraud filter tuned too aggressively
# ---------------------------------------------------------------------------
#
# The only incident invisible to a platform split. It is confined to two
# regions, so an agent that only ever segments by platform sees a modest
# across-the-board dip and no explanation. It has to try another dimension.


def _setup_e(w: WorldWriter, t: datetime) -> None:
    w.set_config(
        key="risk.fraud_score_threshold",
        value="41",
        ts=t,
        by="risk-team",
        description="Block orders scoring above this.",
    )


def _fixes_e(a: Action) -> bool:
    if a.target != "risk.fraud_score_threshold":
        return False
    if a.kind == "restore_configuration":
        return True
    if a.kind == "update_configuration":
        try:
            return int(a.params.get("value", 0)) >= 75
        except (TypeError, ValueError):
            return False
    return False


INCIDENT_E = Incident(
    key="fraud_overblock",
    root_cause_label="risk.fraud_score_threshold lowered from 82 to 41, blocking legitimate orders",
    setup=_setup_e,
    effects=[
        Effect(
            scope={"region": "DE"},
            checkout_failure_boost=0.66,
            payment_error_code="RISK_BLOCKED",
            abandon_stage="risk_check",
            log_service="checkout-svc",
            log_message=(
                "order blocked by risk engine: score 52 exceeds threshold 41 (RISK_BLOCKED)"
            ),
            logs_per_minute=9,
        ),
        Effect(
            scope={"region": "FR"},
            checkout_failure_boost=0.61,
            payment_error_code="RISK_BLOCKED",
            abandon_stage="risk_check",
        ),
        # A threshold applies everywhere. Other regions score lower on average
        # so they are hit far less, but they are hit -- which is why the
        # aggregate moves at all and why a region split is what separates it.
        Effect(
            scope={},
            checkout_failure_boost=0.19,
            payment_error_code="RISK_BLOCKED",
            abandon_stage="risk_check",
        ),
    ],
    is_fixed_by=_fixes_e,
    reference_fix=Action("restore_configuration", "risk.fraud_score_threshold"),
    reference_decoy=Action("disable_feature", "risk.strict_velocity_checks"),
    root_cause_tokens=["risk.fraud_score_threshold"],
    known_decoys=["disable_feature:risk.strict_velocity_checks", "rollback_deployment"],
    ticket_templates=[
        ("Order keeps getting cancelled",
         "I have tried to order four times and it says my order could not be processed. "
         "I have been a customer for years.", None),
        ("Am I blocked?", "Every order I place is rejected immediately. Card is fine.", None),
    ],
)


# ---------------------------------------------------------------------------
# Incident F -- resource exhaustion, fixed by a restart
# ---------------------------------------------------------------------------
#
# Affects every platform and every region evenly, so segmentation reveals
# nothing and the infrastructure signals are the only route in. There is a
# recent deployment to that service, but rolling it back does not drain a pool
# that has already leaked -- only a restart does.


def _setup_f(w: WorldWriter, t: datetime) -> None:
    w.add_deployment(
        ref="8486",
        service="payments-svc",
        version="v3.7.2",
        author="t.bergman",
        summary="Payments: raise connection pool ceiling for peak traffic",
        ts=t - timedelta(hours=5),
    )


def _fixes_f(a: Action) -> bool:
    return a.kind == "restart_service" and a.target == "payments-svc"


INCIDENT_F = Incident(
    key="connection_pool_exhaustion",
    root_cause_label="payments-svc connection pool exhausted; connections leaked since deploy 8486",
    setup=_setup_f,
    effects=[
        Effect(
            scope={},
            checkout_failure_boost=0.52,
            payment_error_code="SVC_POOL_TIMEOUT",
            abandon_stage="payment",
            service="payments-svc",
            service_error_rate=0.10,
            latency_multiplier=3.4,
            log_service="payments-svc",
            log_message=(
                "could not acquire connection from pool within 5000ms "
                "(SVC_POOL_TIMEOUT); active=200 idle=0 waiting=87"
            ),
            logs_per_minute=19,
        )
    ],
    is_fixed_by=_fixes_f,
    reference_fix=Action("restart_service", "payments-svc"),
    reference_decoy=Action("rollback_deployment", "8486"),
    root_cause_tokens=["payments-svc"],
    known_decoys=[
        "rollback_deployment:8486",
        "update_configuration:payments.retry.max_attempts",
    ],
    ticket_templates=[
        ("Everything is slow", "Site is crawling and my payment failed twice.", None),
        ("Payment failed repeatedly", "Three attempts, all failed after a long wait.", None),
    ],
)


REGISTRY = {
    i.key: i
    for i in (INCIDENT_A, INCIDENT_B, INCIDENT_C, INCIDENT_D, INCIDENT_E, INCIDENT_F)
}

ALL_KEYS = list(REGISTRY)
